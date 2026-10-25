"""Controlled evaluation artwork, explicitly SIMULATED. No restaurant's menu is fabricated as real."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def create(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    cases = []
    font_path = 'C:/Windows/Fonts/arial.ttf'
    bold_path = 'C:/Windows/Fonts/arialbd.ttf'
    def make(name, size, entries, items, coverage='unknown', group=None):
        image = Image.new('RGB', size, 'white')
        draw = ImageDraw.Draw(image)
        truth = []
        for text, x, y, font_size, bold in entries:
            font = ImageFont.truetype(bold_path if bold else font_path, font_size)
            draw.text((x, y), text, fill='black', font=font)
            truth.append(text)
        image.save(root/f'{name}.png')
        cases.append(dict(name=name, image=str((root/f'{name}.png').resolve()),
            provenance='SIMULATED_ARTWORK_REAL_OCR', text=truth, items=items,
            expectedRows=[' '.join(e[0] for e in sorted(entries, key=lambda e: e[1]) if e[2]==y)
                          for y in sorted({e[2] for e in entries})],
            coverage=coverage, group=group or name))
        return image
    entries = [('SIMULATED CAFE MENU',60,25,44,True),('MAINS',60,115,36,True),
        ('Garden Pasta',60,185,38,False),('$12.50',850,185,38,False),
        ('Tomato, basil and cheese.',60,235,28,False),('Contains milk.',60,275,28,False),
        ('Peanut Noodles',60,345,38,False),('$11.00',850,345,38,False),
        ('Rice noodles with peanut sauce.',60,395,28,False),('Contains peanuts.',60,435,28,False),
        ('DRINKS',60,535,36,True),('Iced Tea',60,605,38,False),('$3.00',850,605,38,False),
        ('Unsweetened black tea.',60,655,28,False)]
    items = [dict(name='Garden Pasta',price='$12.50',section='MAINS'),
             dict(name='Peanut Noodles',price='$11.00',section='MAINS'),
             dict(name='Iced Tea',price='$3.00',section='DRINKS')]
    image = make('printed_menu', (1200,850), entries, items)
    two = [('SIMULATED TWO COLUMN MENU',60,25,44,True),('BURGERS',60,120,36,True),('DRINKS',1020,120,36,True),
        ('Classic Burger',60,200,38,False),('$8.50',730,200,38,False),
        ('Beef and tomato.',60,250,28,False),('Iced Tea',1020,200,38,False),('$3.00',1690,200,38,False),
        ('Unsweetened black tea.',1020,250,28,False),
        ('Cheese Burger',60,340,38,False),('$9.00',730,340,38,False),('Contains milk.',60,390,28,False),
        ('Sparkling Water',1020,340,38,False),('$2.50',1690,340,38,False),
        ('DESSERTS',60,540,36,True),('Vanilla Cake',60,620,38,False),('$5.00',730,620,38,False)]
    two_items = [dict(name='Classic Burger',price='$8.50',section='BURGERS'), dict(name='Cheese Burger',price='$9.00',section='BURGERS'),
                 dict(name='Iced Tea',price='$3.00',section='DRINKS'), dict(name='Sparkling Water',price='$2.50',section='DRINKS'),
                 dict(name='Vanilla Cake',price='$5.00',section='DESSERTS')]
    make('multi_column', (1900,900), two, two_items)
    make('kiosk', (1200,850), [('SIMULATED KIOSK MENU',60,25,44,True),('BURGERS',60,120,36,True),
        ('Cheeseburger',60,200,38,False),('$5.00',850,200,38,False),
        ('Double Cheeseburger',60,340,38,False),('$7.50',850,340,38,False),
        ('Cancel Order',60,700,32,False)], [dict(name='Cheeseburger',price='$5.00',section='BURGERS'),
                                            dict(name='Double Cheeseburger',price='$7.50',section='BURGERS')])
    make('elevator', (900,850), [('SIMULATED ELEVATOR PANEL',40,25,38,True),
        ('5',120,200,70,False),('6',500,200,70,False),('Door Open',70,450,38,False),
        ('Alarm',500,450,38,False)], [])
    small = [(text, round(x*.48), round(y*.48), max(10, round(font_size*.33)), bold)
             for text,x,y,font_size,bold in entries]
    make('small_text', (600,430), small, items)
    partial = image.crop((0, 100, 1100, 430))
    partial.save(root/'incomplete.png')
    cases.append(dict(name='incomplete',image=str((root/'incomplete.png').resolve()),
        provenance='SIMULATED_ARTWORK_REAL_OCR', text=[e[0] for e in entries[1:9]],
        expectedRows=['MAINS','Garden Pasta $12.50','Tomato, basil and cheese.','Contains milk.',
                      'Peanut Noodles $11.00','Rice noodles with peanut sauce.'],
        items=items[:2],coverage='partial',group='printed_menu'))
    make('similar_names', (1200,700), [('SIMULATED SOUP MENU',60,25,44,True),('SOUPS',60,120,36,True),
        ('Small Tomato Soup',60,200,38,False),('$4.50',850,200,38,False),
        ('Large Tomato Soup',60,340,38,False),('$6.50',850,340,38,False)],
        [dict(name='Small Tomato Soup',price='$4.50',section='SOUPS'),dict(name='Large Tomato Soup',price='$6.50',section='SOUPS')])
    return cases
