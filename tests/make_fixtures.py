"""Writes small test games with planted bugs into tests/fixtures/.
Each folder gets an expect.json listing findings that MUST appear and must NOT appear."""
import html
import json
import os
import shutil
import struct
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, 'fixtures')


def png_bytes(w=2, h=2):
    raw = b''.join(b'\x00' + b'\xff\x00\x00\xff' * w for _ in range(h))

    def chunk(t, d):
        return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)) + \
        chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mode = 'wb' if isinstance(data, bytes) else 'w'
    with open(path, mode, **({} if mode == 'wb' else {'encoding': 'utf-8', 'newline': '\n'})) as f:
        f.write(data)


def twine(fmt, passages, script='', style='', start=1):
    out = []
    for n, (name, tags, text) in enumerate(passages, 1):
        out.append(f'<tw-passagedata pid="{n}" name="{html.escape(name)}" tags="{tags}" position="0,0" size="100,100">'
                   f'{html.escape(text)}</tw-passagedata>')
    return (f'<!DOCTYPE html><html><head><title>Test</title></head><body>\n'
            f'<tw-storydata name="Test" startnode="{start}" creator="Twine" creator-version="2.6" format="{fmt}" '
            f'format-version="1.0" ifid="11111111-2222-3333-4444-555555555555" options="" tags="" zoom="1" hidden>'
            # Twine writes the story stylesheet and script as they are, not HTML-escaped
            f'<style role="stylesheet" id="twine-user-stylesheet" type="text/twine-css">{style}</style>'
            f'<script role="script" id="twine-user-script" type="text/twine-javascript">{script}</script>\n'
            + '\n'.join(out) + '\n</tw-storydata></body></html>\n')


def media_files(d):
    write(os.path.join(d, 'images', 'hero.png'), png_bytes())
    write(os.path.join(d, 'images', 'Castle.png'), png_bytes())
    write(os.path.join(d, 'images', 'cut.png'), png_bytes()[:-12])              # cut off
    write(os.path.join(d, 'images', 'fake.png'), b'<!DOCTYPE html><html>Error 404</html>')
    write(os.path.join(d, 'images', 'unused.png'), png_bytes())
    write(os.path.join(d, 'images', 'photo.jpg'), png_bytes())                  # PNG named .jpg


def spec(d, open_, must, must_not=(), must_not_text=()):
    # must_not_text: words that must not be in any finding's explanation or suggested fix
    write(os.path.join(d, 'expect.json'), json.dumps(dict(open=open_, must=must, must_not=list(must_not),
                                                          must_not_text=list(must_not_text)), indent=1))


def sugarcube():
    d = os.path.join(FIX, 'sugarcube_bugs')
    media_files(d)
    passages = [
        ('Start', '', '<<set $gold to 5>><<set $Gold to 1>><<set $energy to 10>>\n'
                      '[[Go to the town|Town]]\n[[Visit|Castel]]\n<<include "Missing scene">>\n'
                      '<img src="images/hero.png"><img src="images/castle.png"><img src="images/nothere.png">\n'
                      '<img src="images/cut.png"><img src="images/fake.png"><img src="images/photo.jpg">\n'
                      '<img src="C:\\Users\\dev\\game\\images\\hero.png">\n'
                      '<div class="speach">hi</div>'),
        ('Town', '', '<<if $gold = 5>>Rich<</if>>\n<<if $gold is 1 or 2>>x<</if>>\n'
                     '<<if $a and $b or $c>>y<</if>>\n<<set $energy - 20>>\n<<set $days==0>>\n'
                     '<<elesif $gold>>\n<<if $gold gt 1>>open\n'
                     '<<speech>>Hello<</speech>>\n<</speech>>\nThe the end.\nIt was for for nothing.\n'
                     'PLACEHOLDER\nI recieve it.\n$mother-name is here.\n<</if>\n'
                     '<<goto "Town">>>\n<img src="images/hero.png">>\n[[Back|Start]]'),
        ('Unclosed', '', '<<if $gold>>Never closed. [[Start]]'),
        ('Castle', '', 'You are in the castle. [[Start]] <<print $energyy>> <<print $energyy>>'),
        ('Lonely', '', 'Nobody comes here.'),
        ('Empty', '', ''),
        ('Widgets', 'widget', '<<widget "speech" container>><div class="speech">_contents</div><</widget>>'),
        ('Broken script', 'script', 'Macro.add("x", { handler() { if (a { } } });'),
        ('Old', '', '<<click "Go">><<goto "Start">><</click>> <<display "Castle">> [[Empty]] [[Unclosed]]'),
    ]
    write(os.path.join(d, 'game.html'), twine('SugarCube', passages, style='.speech { color: red }'))
    spec(d, 'game.html', [
        'Error|Broken links|Link to missing passage "Castel"', '<<include>> to missing passage "Missing scene"',
        'Error|Missing files|Image not found: images/nothere.png', 'Warning|Missing files|Image path has wrong capital letters: images/castle.png',
        "Image uses a full path from someone's PC", 'Error|Broken files|Damaged image (cut off): images/cut.png',
        'Error|Broken files|Not a real image: images/fake.png', 'Info|Broken files|1 file(s) have the wrong file extension',
        'Info|Unused files|', 'Single "=" inside a condition', '"$gold is 1 or 2" does not compare both',
        'Condition mixes "and" and "or" without brackets', '<<set>> does not change anything', '<<set>> compares instead of setting',
        'Unknown macro <<elesif>>', '<<if>> is never closed', '<</speech>> has no opening', 'Closing tag <</if> is missing a ">"',
        'Extra ">" after a macro tag', 'Stray ">" after an HTML tag', 'Repeated word "for for"', '"recieve" → "receive"',
        '"PLACEHOLDER" text shown to players', '"$mother-name" is read as', '$energyy is used but never set',
        '$Gold and $gold differ only in capital letters', 'Class "speach" does not match any style',
        'Passage "Empty" is empty', 'JavaScript|JavaScript brackets/quotes problem', '<<click>> is an old SugarCube macro',
        'Passage "Lonely" is never linked',
    ])


def clean_sugarcube():
    d = os.path.join(FIX, 'clean_sugarcube')
    write(os.path.join(d, 'images', 'hero.png'), png_bytes())
    passages = [
        ('Start', '', '<<set $gold to 5>>\n<<if $gold gte 5>>You are rich.<<else>>You are poor.<</if>>\n'
                      '<img src="images/hero.png">\n[[Go to town|Town]]'),
        ('Town', '', '<<if ($gold is 5 or $gold is 6) and $gold lt 10>>Nice.<</if>>\nYou have $gold coins. [[Start]]'),
    ]
    write(os.path.join(d, 'game.html'), twine('SugarCube', passages))
    spec(d, 'game.html', [], must_not=['Error|', 'Warning|'])


def harlowe():
    d = os.path.join(FIX, 'harlowe_bugs')
    write(os.path.join(d, 'images', 'hero.png'), png_bytes())
    passages = [
        ('Start', '', '(set: $gold to 5)(set: $name = "Bob")\n(if: $gold is 5)[Rich](else:)[Poor]\n(if: $gold = 3)[x]\n'
                      '(prnt: $gold)\n(link-goto: "Go", "Twon")\n(if: $gold > 1)\n[[Town]]\n[unclosed hook\n'
                      '<img src="images/hero.png"><img src="images/gone.png">\n(print: $goldd)(print: $goldd)'),
        ('Town', '', 'Town. [[Start]] (display: "Start")'),
    ]
    write(os.path.join(d, 'game.html'), twine('Harlowe', passages))
    spec(d, 'game.html', ['Unknown macro (prnt:)', 'Harlowe link to missing passage "Twon"', '(set:) uses "=" instead of "to"',
                          'Single "=" inside a condition', '(if:) has no hook', 'Hook "[" is never closed', '$goldd is used but never set',
                          'Image not found: images/gone.png'],
         must_not=['Unknown macro (if:)', 'Unknown macro (else:)', 'Unknown macro (set:)'])


def harlowe_paths():
    """Things a big game does that must not be reported as broken (found in X-Change Life)."""
    d = os.path.join(FIX, 'harlowe_paths')
    write(os.path.join(d, 'img', 'minigame', '001', '1 thick.jpg'), png_bytes())
    write(os.path.join(d, 'img', 'places', 'bar.jpg'), png_bytes())
    write(os.path.join(d, 'img', 'places', 'door.jpg'), png_bytes())
    write(os.path.join(d, 'aud', 'song.opus'), b'OggS' + b'\x00' * 60)
    write(os.path.join(d, 'aud', 'rain.ogg'), b'OggS' + b'\x00' * 60)
    write(os.path.join(d, 'aud', 'bell.opus'), b'OggS' + b'\x00' * 60)
    script = ('const base="img/minigame/";const pics={"001":{images:["001/1 thick.jpg"]}};\n'  # folder added in code
              'const lost=["zzz/nothing here.jpg"];\n'
              'const fmts=new Set(["","mp3","opus"]);\n'  # the game switches mp3 -> opus itself
              'function q(e){e=e.replace(/&quot;/g,\'"\');return e.replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;"})[c])}\n'
              'const t=`a${fmts.size?`b${1}`:"c"}d`;\n')
    passages = [
        ('Start', '', '<audio src="aud/song.mp3"></audio><audio src="aud/rain.wav"></audio>\n'
                      '<img src="pics/bell.jpg">\n(link-goto: "Storm", "storm night")\n'
                      '(set: _h to "<script>" + "go(" + "</script>")\n'
                      '(set: $pic to "places/bar.jpg")<img src="places/door.jpg">'),  # macros add "img/" in front
        ('storm night', '', '(if: $fem)[(display: "storm night girl")](else:)[(display: "storm night guy")]'),
        ('storm night girl', '', 'Thunder. [[Start]]'),
    ]
    write(os.path.join(d, 'game.html'), twine('Harlowe', passages, script=script))
    spec(d, 'game.html', ['Warning|Missing files|Image not found: zzz/nothing here.jpg',
                          'Warning|Missing files|Audio not found: aud/rain.wav (a ogg file is there instead)',
                          'Error|Missing files|Image not found: pics/bell.jpg',
                          'Error|Missing files|Image not found: places/door.jpg',
                          'Harlowe link to missing passage "storm night guy"'],
         must_not=['Image not found: 001/1 thick.jpg', 'Image not found: places/bar.jpg', 'Audio not found: aud/song.mp3', 'JavaScript brackets/quotes problem'],
         must_not_text=['probably "aud/bell.opus"', 'Did you mean "storm night"?'])


def chapbook():
    d = os.path.join(FIX, 'chapbook_bugs')
    passages = [
        ('Start', '', 'gold: 5\nname: "Bob"\n--\nYou have {gold} gold and {goldd} too.\n{link to: \'Town\', \'Go\'}\n'
                      '{lnk to: \'Town\', \'Go\'}\n{link to: \'Nowhere\', \'Lost\'}\n[if gold = 5]\nRich.\n[continue]\n{unclosed'),
        ('Town', '', 'Town. {back link}'),
    ]
    write(os.path.join(d, 'game.html'), twine('Chapbook', passages))
    spec(d, 'game.html', ['Unknown insert {lnk to', 'Chapbook link to missing passage "Nowhere"', 'Single "=" inside a condition',
                          'goldd is used but never set', 'Insert "{" is never closed'],
         must_not=['gold is used but never set', 'Unknown insert {link to'])


def snowman():
    d = os.path.join(FIX, 'snowman_bugs')
    passages = [
        ('Start', '', '<% s.gold = 5; %>\nYou have <%= s.gold %> and <%= s.goldd %>.\n<% if (s.gold > 1) { %>rich<% } %>\n'
                      '[[Town]] <% story.show("Nowhere") %>\n<% if (s.gold { %>\n'),
        ('Town', '', 'Town. [[Start]]'),
    ]
    write(os.path.join(d, 'game.html'), twine('Snowman', passages))
    spec(d, 'game.html', ['s.goldd is used but never set', 'Script link to missing passage "Nowhere"', 'Script code problem'],
         must_not=['s.gold is used but never set'])


def plain_html():
    d = os.path.join(FIX, 'plain_html')
    write(os.path.join(d, 'img', 'logo.png'), png_bytes())
    write(os.path.join(d, 'index.html'),
          '<!DOCTYPE html><html><head><style>.box{color:red}</style></head><body>\n'
          '<img src="img/logo.png"><img src="img/missing.png"><img src="img/logo.png>\n'
          '<a href="#nowhere">jump</a><div class="boxx">x</div>\n'
          '<script>function f() { if (a { return 1; } }</script>\n</body></html>\n')
    spec(d, 'index.html', ['Image not found: img/missing.png', 'Link to missing page anchor "#nowhere"',
                           '<img> tag has an unclosed quote', 'JavaScript brackets/quotes problem', 'Class "boxx" does not match'])


def renpy():
    d = os.path.join(FIX, 'renpy_bugs')
    g = os.path.join(d, 'game')
    write(os.path.join(g, 'images', 'eileen happy.png'), png_bytes())
    write(os.path.join(g, 'images', 'bg room.png'), png_bytes())
    write(os.path.join(g, 'audio', 'theme.ogg'), b'OggS' + b'\x00' * 60)
    write(os.path.join(g, 'script.rpy'),
          'define e = Character("Eileen")\ndefault gold = 5\n\nlabel start:\n    scene bg room\n    show eileen happy\n'
          '    show eilen sad\n    play music "audio/theme.ogg"\n    play sound "audio/boom.ogg"\n'
          '    e "You have [gold] gold. PLACEHOLDER"\n    if goldd > 3\n        e "Rich"\n\tjump chapter_two\n'
          '    jump chapter_too\n    call screen inventori\n\nlabel chapter_two:\n    e "Hi"\n    return\n\n'
          'label chapter_two:\n    return\n\nlabel lonely:\n    return\n\nscreen inventory():\n    text "Items"\n')
    spec(d, 'game', ['Tab character used for indentation', '"if" line is missing ":"', 'Jump to missing label "chapter_too"',
                     'Label "chapter_two" is defined twice', 'Screen "inventori" does not exist', 'Audio not found: audio/boom.ogg',
                     '"goldd" looks like a misspelling of "gold"', 'Image "eilen sad" is not defined', 'Label "lonely" is never',
                     '"PLACEHOLDER" text shown'],
         must_not=['Image "eileen happy"', 'Image "bg room"', 'Audio not found: audio/theme.ogg'])


def rpgmaker():
    d = os.path.join(FIX, 'rpgmaker_bugs')
    write(os.path.join(d, 'img', 'characters', 'Actor1.png'), png_bytes())
    write(os.path.join(d, 'img', 'faces', 'Actor1.png'), png_bytes())
    write(os.path.join(d, 'audio', 'bgm', 'Theme1.ogg'), b'OggS' + b'\x00' * 60)
    system = dict(switches=['', 'Door open', ''], variables=['', 'Gold'], title1Name='', titleBgm=dict(name='Theme1'))
    write(os.path.join(d, 'data', 'System.json'), json.dumps(system))
    write(os.path.join(d, 'data', 'MapInfos.json'), json.dumps([None, dict(id=1, name='Town'), dict(id=2, name='Gone')]))
    write(os.path.join(d, 'data', 'CommonEvents.json'), json.dumps([None, dict(id=1, name='Heal', list=[])]))
    ev = dict(id=1, name='Guard', pages=[dict(image=dict(characterName='Actor2'), conditions=dict(switch1Valid=True, switch1Id=5),
                                              list=[dict(code=101, parameters=['Actor1', 0, 0, 2]),
                                                    dict(code=401, parameters=['TODO write this']),
                                                    dict(code=241, parameters=[dict(name='Missing tune')]),
                                                    dict(code=201, parameters=[0, 9, 1, 1, 0, 0]),
                                                    dict(code=117, parameters=[7]),
                                                    dict(code=121, parameters=[2, 2, 0])])])
    write(os.path.join(d, 'data', 'Map001.json'), json.dumps(dict(events=[None, ev], bgm=dict(name='Theme1'))))
    write(os.path.join(d, 'js', 'plugins.js'), 'var $plugins =\n[\n{"name":"GoodPlugin","status":true,"description":"","parameters":{}},\n'
                                               '{"name":"GonePlugin","status":true,"description":"","parameters":{}},\n'
                                               '{"name":"BadPlugin","status":true,"description":"","parameters":{}}\n];\n')
    write(os.path.join(d, 'js', 'plugins', 'GoodPlugin.js'), '(function(){ var a = 1; })();\n')
    write(os.path.join(d, 'js', 'plugins', 'BadPlugin.js'), '(function(){ if (a { })();\n')
    spec(d, '.', ['Image not found: img/characters/Actor2', 'Audio not found: audio/bgm/Missing tune', 'Switch #5 does not exist',
                  'Transfer to missing map #9', 'Call to missing common event #7', 'Map file missing: Map002.json',
                  'Plugin file missing: GonePlugin.js', 'Plugin BadPlugin.js', 'Switch #2 has no name', 'Placeholder text shown'],
         must_not=['Image not found: img/faces/Actor1', 'Audio not found: audio/bgm/Theme1', 'GoodPlugin'])


def ink():
    d = os.path.join(FIX, 'ink_bugs')
    write(os.path.join(d, 'story.ink'),
          'INCLUDE extra.ink\nINCLUDE gone.ink\nVAR gold = 5\n\nYou wake up. {gold} coins.\n* [Go] -> town\n* [Lost] -> twon\n\n'
          '=== town ===\nTown. {goldd}\n~ goldd = 3\n~ silver = 1\n-> END\n\n=== lonely ===\nNobody. TODO\n-> DONE\n\n=== broken ===\n{gold > 1: rich\n-> END\n')
    write(os.path.join(d, 'extra.ink'), '=== extra ===\nExtra.\n-> END\n')
    spec(d, 'story.ink', ['Included file not found: gone.ink', 'Divert to missing knot/stitch "twon"',
                          'Assignment to undeclared variable "goldd"', 'Assignment to undeclared variable "silver"',
                          '"{" is never closed', '"TODO" text shown'],
         must_not=['Divert to missing knot/stitch "town"', 'Divert to missing knot/stitch "END"'])


def choicescript():
    d = os.path.join(FIX, 'choicescript_bugs')
    s = os.path.join(d, 'scenes')
    write(os.path.join(s, 'startup.txt'), '*title Test\n*scene_list\n  startup\n  chapter1\n  chapter2\n\n*create gold 5\n*create name "Bob"\n\n'
                                           'Hello ${name}, you have ${goldd} gold.\n*goto_scene chapter1\n')
    write(os.path.join(s, 'chapter1.txt'), '*label top\n*set gold +1\n*set silver 2\n*got top\n*goto bottom\n'
                                            '*goto_scene chaptr2\n*goto_scene startup nolabel\n*if gold > 1\n  Rich. TODO\n\t*finish\n*finish\n')
    spec(d, '.', ['Scene "chapter2" in the scene list has no file', '${goldd} is not a declared variable',
                  '*set on undeclared variable "silver"', 'Unknown command *got', '*goto to missing label "bottom"',
                  '*goto_scene to missing scene "chaptr2"', '*goto_scene to missing label "nolabel"',
                  'Tabs and spaces mixed', '"TODO" text shown'],
         must_not=['*set on undeclared variable "gold"', '*goto to missing label "top"'])


if __name__ == '__main__':
    if os.path.isdir(FIX):
        shutil.rmtree(FIX)
    for fn in (sugarcube, clean_sugarcube, harlowe, harlowe_paths, chapbook, snowman, plain_html, renpy, rpgmaker, ink, choicescript):
        fn()
    print('fixtures written to', FIX)
