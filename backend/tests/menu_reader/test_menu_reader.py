"""SIMULATED OCR spans; actual OCR is measured separately, not mocked as live validation."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from menu_reader.layout import document
from menu_reader.questions import MenuSession, ReadingCursor, reading_chunks
from menu_reader.gemini_baseline import run as cloud_baseline


def page(page_id='SIMULATED-page',currency='$',extra=()):
    words=[]
    def line(text,x,y,height=24):
        for token in text.split():
            width=len(token)*12
            words.append(dict(id=f'{page_id}-{len(words)}',text=token,box=[x,y,width,height],confidence=None,pageId=page_id))
            x+=width+10
    for row in [('CAFE MENU',50,10,32),('BURGERS',50,80,24),('Classic Burger',50,140,24),
                (currency+'8.50',800,140,24),('Beef and tomato.',50,180,16),
                ('Double Burger',50,260,24),(currency+'10.00',800,260,24),
                ('Contains peanuts.',50,300,16),*extra]:line(*row)
    return dict(pageId=page_id,image={'width':1000,'height':700},words=words,coverage='partial',
                provenance='SIMULATED',timing={'wallMs':0})


class MenuLayoutTests(unittest.TestCase):
    def test_exact_items_prices_sections_and_source_spans(self):
        p=page(); menu=document([p])
        self.assertEqual([(i['name'],i['price']['raw']) for i in menu['items']],
                         [('Classic Burger','$8.50'),('Double Burger','$10.00')])
        self.assertEqual(menu['sections'],['BURGERS'])
        self.assertFalse(menu['complete'])
        ids={w['id'] for w in p['words']}
        self.assertTrue(all(set(i['sourceIds'])<=ids for i in menu['items']))
        self.assertTrue(all(not i['reviewed'] for i in menu['items']))

    def test_descriptions_and_explicit_notes_remain_exact(self):
        menu=document([page()])
        self.assertEqual(menu['items'][0]['description'][0]['text'],'Beef and tomato.')
        self.assertEqual(menu['items'][1]['printedNotes'][0]['text'],'Contains peanuts.')

    def test_duplicate_capture_deduplicates_exact_facts_and_retains_provenance(self):
        menu=document([page('SIMULATED-a'),page('SIMULATED-b')])
        self.assertEqual(len(menu['items']),2)
        self.assertEqual(menu['items'][0]['pageIds'],['SIMULATED-a','SIMULATED-b'])

    def test_similar_names_different_prices_are_not_merged(self):
        menu=document([page()])
        self.assertEqual(len(menu['items']),2)
        self.assertEqual(MenuSession(menu).answer('Burger')['status'],'CLARIFY')

    def test_multiple_price_variants_and_vertical_price_remain_unresolved(self):
        p=page(extra=[('Soup Small $4.00 Large $6.00',50,400,24),('$7.00',50,480,24)])
        menu=document([p])
        self.assertEqual(len(menu['items']),2)
        self.assertEqual(len(menu['unstructured']),2)
        self.assertIn('multiple_prices',menu['unstructured'][0]['reason'])

    def test_total_is_not_a_dish(self):
        menu=document([page(extra=[('Total $18.50',50,400,24)])])
        self.assertEqual(len(menu['items']),2)
        self.assertEqual(menu['unstructured'][0]['reason'],'transaction_line_not_menu_item')

    def test_unpriced_name_does_not_attach_as_previous_description(self):
        menu=document([page(extra=[('Mystery Dish',50,420,24)])])
        self.assertIn('Mystery Dish',[r['text'] for r in menu['unstructured']])

    def test_geometry_not_input_order_determines_rows(self):
        p=page();p['words']=list(reversed(p['words']))
        self.assertEqual([i['name'] for i in document([p])['items']],['Classic Burger','Double Burger'])

    def test_conflicting_menu_titles_require_scope_confirmation(self):
        a,b=page('SIMULATED-a'),page('SIMULATED-b')
        b['words'][0]['text']='OTHER'
        menu=document([a,b]);session=MenuSession(menu)
        self.assertTrue(menu['hasTitleConflict'])
        self.assertEqual(session.answer('cheapest')['status'],'CLARIFY')
        session.answer('confirm same menu')
        self.assertEqual(session.answer('cheapest')['status'],'PROVISIONAL')
        self.assertFalse(menu['complete'])

    def test_invalid_boxes_are_not_repaired_into_item_locations(self):
        p=page()
        p['words'][2]['box']=[float('nan'),0,5,5]
        menu=document([p])
        self.assertTrue(any('invalid/unlocated' in w for w in menu['warnings']))
        self.assertFalse(menu['complete'])

    def test_native_candidate_confidence_not_misreported_as_word_confidence(self):
        p=page()
        for word in p['words']:word['lineCandidateConfidence']=.99
        menu=document([p])
        self.assertTrue(all(not i['reviewed'] for i in menu['items']))


class MenuQuestionTests(unittest.TestCase):
    def setUp(self):self.session=MenuSession(document([page()]))

    def test_unknown_allergen_never_inferred_from_dish_name_or_absence(self):
        answer=self.session.answer('Does Classic Burger contain peanuts?')
        self.assertEqual(answer['status'],'ABSTAIN')
        self.assertIn('Ask staff',answer['text'])
        self.assertNotIn('peanut-free',answer['text'])

    def test_explicit_allergen_is_quoted_not_declared_safe(self):
        answer=self.session.answer('Does Double Burger contain peanuts?')
        self.assertEqual(answer['status'],'PRINTED_INFORMATION_ONLY')
        self.assertIn('Contains peanuts.',answer['text'])
        self.assertIn('cross-contact',answer['text'])

    def test_cheapest_is_provisional_until_explicitly_reviewed(self):
        answer=self.session.answer('Which is cheapest?')
        self.assertEqual(answer['status'],'PROVISIONAL')
        self.assertIn('Classic Burger',answer['text'])
        item=self.session.menu['items'][0]
        self.session.answer('review '+item['id'])
        self.assertEqual(self.session.answer('Which is cheapest?')['status'],'REVIEWED_CAPTURE_ONLY')

    def test_mixed_or_absent_currency_refuses_comparison(self):
        self.session.menu['items'][1]['price']['currency']='€'
        self.assertEqual(self.session.answer('Cheapest?')['status'],'ABSTAIN')

    def test_session_keeps_facts_without_camera(self):
        self.session.answer('Tell me about Double Burger')
        self.assertEqual(self.session.answer('Does this contain peanuts?')['status'],'PRINTED_INFORMATION_ONLY')

    def test_order_is_draft_only_and_requires_confirmation(self):
        self.session.answer('I choose Classic Burger')
        answer=self.session.answer('Help me communicate with the cashier')
        self.assertEqual(answer['status'],'DRAFT')
        self.assertTrue(answer['requiresConfirmation'])
        self.assertEqual(answer['action'],'NO_AUDIO_OR_ORDER_PLACED')
        self.assertEqual(self.session.answer('confirm order')['action'],'TEXT_ONLY_NO_AUDIO_OR_ORDER_PLACED')

    def test_pause_resume_skip_stop_do_not_repeat_chunks(self):
        cursor=ReadingCursor(['one','two','three'])
        self.assertEqual(cursor.next(),'one');cursor.pause()
        self.assertEqual(cursor.next(),'Reading paused.');cursor.resume();cursor.skip()
        self.assertEqual(cursor.next(),'three');cursor.stop()
        self.assertEqual(cursor.next(),'End of captured text.')

    def test_api_benchmark_is_opt_in(self):
        with patch('urllib.request.urlopen',side_effect=AssertionError('no network')):
            with self.assertRaises(ValueError):cloud_baseline(Path('unused'),Path('unused'),allow_api=False)


if __name__=='__main__':unittest.main()
