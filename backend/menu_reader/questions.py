"""Grounded, deliberately limited natural-language intents. No generative ingredient guesses."""
from decimal import Decimal
import re


def reading_chunks(menu, section=None):
    yield 'This is the visible captured text; the complete menu has not been confirmed.'
    if menu.get('title'):
        yield menu['title']
    if menu['kind']=='textOnly':
        for page in menu['parsedPages']:
            for line in page['lines']:
                yield line['text']
        return
    for category in menu['sections']+(['Uncategorized'] if any(i['section']=='Uncategorized' for i in menu['items']) else []):
        if section and category.casefold() != section.casefold():
            continue
        items = [i for i in menu['items'] if i['section']==category]
        if items:
            yield category
        for item in items:
            yield item['name']+'. '+item['price']['raw']+'. '+' '.join(d['text'] for d in item['description']+item['printedNotes'])
    if not section and menu['unstructured']:
        yield 'Unstructured or uncertain text follows; item and price relationships are not confirmed.'
        for row in menu['unstructured']:
            yield row['text']


class ReadingCursor:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.index = 0
        self.paused = False
    def next(self):
        if self.paused:
            return 'Reading paused.'
        if self.index >= len(self.chunks):
            return 'End of captured text.'
        value = self.chunks[self.index]
        self.index += 1
        return value
    def pause(self):
        self.paused = True
    def resume(self):
        self.paused = False
    def skip(self):
        self.index = min(len(self.chunks), self.index+1)
    def stop(self):
        self.index = len(self.chunks)


class MenuSession:
    def __init__(self, menu):
        self.menu = menu
        self.current_item = None
        self.current_section = None
        self.order_draft = None
        self.cursor = ReadingCursor(reading_chunks(menu))

    def answer(self, question):
        q = question.strip().casefold()
        def reply(text, status='GROUNDED', ids=(), **extra):
            return dict(text=text, status=status, sourceIds=list(ids), **extra)
        if q in ('stop', 'pause', 'resume', 'skip', 'next'):
            if q == 'pause': self.cursor.pause()
            elif q == 'resume': self.cursor.resume()
            elif q == 'skip': self.cursor.skip()
            elif q == 'stop': self.cursor.stop()
            return reply(self.cursor.next() if q in ('next','resume') else 'Reading '+q+'.', status='READING_CONTROL')
        if q.startswith('review '):
            item = next((i for i in self.menu['items'] if i['id']==question.strip()[7:].strip()), None)
            if not item: return reply('Unknown item ID.', 'CLARIFY')
            item['reviewed'] = True
            return reply('Marked the recognized name and price as explicitly reviewed by the user.', 'USER_CONFIRMED', item['sourceIds'])
        if q in ('confirm order', 'confirm-order'):
            if not self.order_draft: return reply('No order draft to confirm.', 'CLARIFY')
            return reply(self.order_draft, 'USER_CONFIRMED', requiresSpeechDispatch=True,
                         action='TEXT_ONLY_NO_AUDIO_OR_ORDER_PLACED')
        if q=='confirm same menu':
            self.menu['hasTitleConflict']=False
            return reply('Recorded your confirmation that these captures belong to the same menu; scan completeness remains unknown.', 'USER_CONFIRMED')
        if 'section' in q or 'categor' in q or q in ('read the menu', 'read menu'):
            return reply('Recognized sections: '+', '.join(self.menu['sections'])+'. Would you like all captured text or a section?')
        section = next((s for s in self.menu['sections'] if s.casefold() in q or s.casefold().rstrip('s') in q.split()), None)
        if section: self.current_section = section
        relevant = [i for i in self.menu['items'] if not self.current_section or i['section']==self.current_section]
        tokens = set(re.findall(r'\w+', q))
        explicit = [i for i in self.menu['items'] if set(re.findall(r'\w+', i['name'].casefold())).issubset(tokens)]
        if not explicit:
            content = tokens-{'what','does','contain','tell','about','this','have','read','the','me','do','they',
                              'is','a','an','please','find','where','can','i'}
            if content:
                explicit = [i for i in self.menu['items'] if content.issubset(set(re.findall(r'\w+', i['name'].casefold())))]
        if len(explicit)==1:
            self.current_item = explicit[0]
        allergen = re.search(r'\b(peanuts?|nuts?|milk|dairy|eggs?|soy|sesame|gluten|shellfish)\b', q)
        if allergen and any(w in q for w in ('contain', 'allerg', 'free', 'safe', 'ingredient', 'does')):
            item = self.current_item
            if not item or len(explicit)>1:
                return reply('Which specific dish do you mean?', 'CLARIFY')
            word = allergen.group().rstrip('s')
            notes = item['printedNotes']+item['description']
            found = [n for n in notes if re.search(r'\b'+re.escape(word)+r's?\b', n['text'], re.I)]
            if found:
                return reply('The captured text for '+item['name']+' says: '+' '.join(n['text'] for n in found)+
                    ' This does not confirm current preparation or cross-contact; ask staff.', 'PRINTED_INFORMATION_ONLY',
                    [s for n in found for s in n['sourceIds']])
            return reply('The captured menu text does not confirm that information for '+item['name']+
                '. Ask staff about ingredients, allergens and cross-contact.', 'ABSTAIN', item['sourceIds'])
        if any(w in q for w in ('cheapest', 'least expensive', 'lowest price')):
            if self.menu.get('hasTitleConflict'):
                return reply('The photos have conflicting titles. Confirm they belong to the same menu before comparing prices.', 'CLARIFY')
            if not relevant: return reply('No reliably associated prices in this capture.', 'ABSTAIN')
            reviewed = [i for i in relevant if i['reviewed']]
            choices = reviewed or relevant
            currencies = {i['price']['currency'] for i in choices}
            if len(currencies)!=1 or None in currencies:
                return reply('Currencies are missing or mixed; I cannot compare those prices safely.', 'ABSTAIN')
            item = min(choices, key=lambda i: Decimal(i['price']['amount']))
            self.current_item = item
            prefix = 'Among explicitly reviewed captured entries' if reviewed else 'The lowest OCR-read candidate, not a verified cheapest choice,'
            return reply(prefix+' is '+item['name']+' at '+item['price']['raw']+
                '. Other items/pages may be missing; verify the recognized price.',
                'REVIEWED_CAPTURE_ONLY' if reviewed else 'PROVISIONAL', item['sourceIds'])
        if any(w in q for w in ('cashier', 'draft order', 'communicate', 'order this', 'i choose', 'i want')):
            if not self.current_item: return reply('Choose a specific dish first.', 'CLARIFY')
            self.order_draft = 'I would like '+self.current_item['name']+'.'
            return reply('Draft only: '+self.order_draft+' Confirm before speaking it to staff.', 'DRAFT',
                         self.current_item['sourceIds'], requiresConfirmation=True, action='NO_AUDIO_OR_ORDER_PLACED')
        if 'read everything' in q or 'read all' in q:
            self.current_section = self.current_item = None
            self.cursor = ReadingCursor(reading_chunks(self.menu))
            return reply('\n'.join(self.cursor.chunks), 'CAPTURED_TEXT_ONLY')
        if len(explicit)>1:
            return reply('Multiple matches: '+', '.join(i['name']+' '+i['price']['raw'] for i in explicit)+'. Please specify.', 'CLARIFY')
        if len(explicit)==1:
            item = explicit[0]
            return reply(item['name']+' '+item['price']['raw']+'. '+
                ' '.join(d['text'] for d in item['description']+item['printedNotes']), 'OCR_UNVERIFIED', item['sourceIds'])
        if section or 'what' in q and ('have' in q or 'options' in q):
            return reply('; '.join(i['name']+' '+i['price']['raw'] for i in relevant) or 'No associated items found.',
                         'OCR_UNVERIFIED', [s for i in relevant for s in i['sourceIds']])
        return reply('This prototype answers sections, listed dishes, prices, cheapest captured candidates and explicitly printed notes. It cannot infer missing facts.', 'UNSUPPORTED_QUESTION')
