"""Teaching texts: glossary, lessons and the first-run tour. All translatable."""
from i18n import T


def glossary():
    """[(word, explanation)] - words are matched in the details text and get a hover explanation."""
    return [
        (T('passage'), T('One page or scene of a Twine game. Each passage has a name, and links go from one passage to another.')),
        (T('scene'), T('One page of the story. In Twine this is called a "passage".')),
        (T('macro'), T('A game command written between << and >>, like <<if>> or <<set>>. The game runs it instead of showing it.')),
        (T('command'), T('An instruction for the game, like "show this picture" or "if the player has 5 gold". In SugarCube they are written between << and >>.')),
        (T('variable'), T('A named value the game remembers, like $gold or $mother_love. It starts with $ in SugarCube.')),
        (T('value'), T('A number, word or yes/no the game remembers, like the amount of gold.')),
        (T('condition'), T('A check like "$gold gte 5" (does the player have at least 5 gold?). The text after it only shows when the check is true.')),
        (T('tag'), T('A marker in code. Opening tags like <<if>> start a part, closing tags like <</if>> end it.')),
        (T('link'), T('Clickable text that takes the player to another scene. In Twine: [[Text|Scene name]].')),
        (T('include'), T('A command that pastes another passage into this one: <<include "Name">>.')),
        (T('widget'), T('A home-made command defined once and reused in many scenes.')),
        (T('HTML'), T('The language web pages are written in. Tags like <img> (picture) and <div> (box).')),
        (T('CSS'), T('Style rules: colours, sizes and layout. A "class" connects an element to a style.')),
        (T('JavaScript'), T('The programming language of web pages. Games use it for special features.')),
        (T('script'), T('A piece of program code (usually JavaScript).')),
        (T('Twine'), T('The free program many text games are made with. It publishes the game as one .html file.')),
        (T('SugarCube'), T('The most common "story format" (engine) for Twine games. It provides the << >> commands.')),
        (T('publish'), T('In Twine: Build > Publish to File. It writes the playable .html file from your story.')),
        (T('file path'), T('Where a file is, like images/hero.png (the folder "images", the file "hero.png").')),
        (T('line'), T('One row of text in the file. Text editors show line numbers on the left.')),
        (T('indent'), T('Spaces at the start of a line. Some engines use them to know which lines belong together.')),
        (T('label'), T('A named spot in a Ren\'Py or ChoiceScript story that other parts can jump to.')),
        (T('knot'), T('A named section of an Ink story. "-> name" jumps to it.')),
        (T('plugin'), T('An extra script added to an RPG Maker game.')),
    ]


def lessons():
    """[(title, [(kind, text)])] where kind is 'p' (paragraph), 'code', 'good' or 'bad'."""
    return [
        (T('1. What is a passage (scene)?'), [
            ('p', T('A Twine game is made of passages. Each passage is one scene: some text, maybe a picture, and links to other passages.')),
            ('p', T('Links look like this. The part before | is what the player sees, the part after | is the name of the passage it opens:')),
            ('code', '[[Go to the kitchen|Kitchen]]'),
            ('p', T('The name must match exactly, including capital letters and spaces. "kitchen" and "Kitchen" are different passages.')),
        ]),
        (T('2. Commands between << and >>'), [
            ('p', T('SugarCube games use commands written between << and >>. The game runs them instead of showing them.')),
            ('code', '<<set $gold to 10>>\n<<if $gold gte 5>>You are rich!<</if>>'),
            ('p', T('Always exactly two < at the start and two > at the end. One too many or too few breaks the command:')),
            ('bad', '<<set $gold to 10>>>\n<</if>'),
            ('good', '<<set $gold to 10>>\n<</if>>'),
        ]),
        (T('3. Opening and closing'), [
            ('p', T('Some commands are like brackets: they open a part and must be closed. <<if>> is closed by <</if>>.')),
            ('good', '<<if $gold gte 5>>\n  You buy the sword.\n<<else>>\n  You cannot afford it.\n<</if>>'),
            ('p', T('If the closing <</if>> is missing, the game shows a red error box. If there is one too many, too.')),
        ]),
        (T('4. Values the game remembers ($variables)'), [
            ('p', T('A word starting with $ is a value the game remembers between scenes, like $gold or $mother_love.')),
            ('p', T('Capital letters count: $Gold and $gold are two different values. A typo creates a new, empty value, so the change is lost.')),
            ('bad', '<<set $Gold to 5>>   <<if $gold gte 5>>'),
            ('good', '<<set $gold to 5>>   <<if $gold gte 5>>'),
            ('p', T('To change a value use "to", "+=" or "-=". A plain "-" calculates but changes nothing:')),
            ('bad', '<<set $energy - 20>>'),
            ('good', '<<set $energy -= 20>>'),
        ]),
        (T('5. "If" checks'), [
            ('p', T('Inside an "if", compare with "is", "isnot", "gte" (at least), "lte" (at most). A single "=" is wrong there:')),
            ('bad', '<<if $gold = 5>>'),
            ('good', '<<if $gold is 5>>'),
            ('p', T('"and" is checked before "or". Use brackets to group, otherwise the check can be true at the wrong time:')),
            ('bad', '<<if $sick is false and $day is 1 or $day is 2>>'),
            ('good', '<<if $sick is false and ($day is 1 or $day is 2)>>'),
        ]),
        (T('6. Pictures and sounds'), [
            ('p', T('Pictures are shown with an <img> tag. The path says where the file is, starting from the game\'s folder:')),
            ('code', '<img src="images/characters/hero.png">'),
            ('p', T('Use / between folders. \\ works on Windows only. Capital letters must match the real file name for phones, Macs and websites.')),
            ('p', T('Every quote mark " that opens must close, or the picture will not show.')),
        ]),
        (T('7. Editing safely'), [
            ('p', T('Before changing anything, make a backup (Tools > Back up the game file).')),
            ('p', T('If the game is made in Twine, make changes in Twine and publish again. Changes made directly in the .html file are lost the next time the game is published from Twine.')),
            ('p', T('If you only have the .html file (no Twine story), edit it with a text editor like Notepad++. Inside the .html file, < and > are written as &lt; and &gt; - the app shows the exact text to copy.')),
            ('p', T('After each change: save, reload the game in the browser, and press "I fixed it - check" in this app.')),
        ]),
        (T('8. Reading this app'), [
            ('p', T('Red = broken: players will run into it. Yellow = probably broken. Blue = worth checking, often fine.')),
            ('p', T('Start with the "Start here" list in the summary, or use the Fix wizard: it walks you through one problem at a time.')),
            ('p', T('"Mark as intentional" hides a finding you have checked and want to keep. It stays hidden for this game.')),
        ]),
    ]


def tour_steps():
    """[(widget attribute name on App, text)]"""
    return [
        ('btn_open', T('Start here: open the game\'s .html file (or a Ren\'Py, RPG Maker, Ink or ChoiceScript game folder). The check starts by itself.')),
        ('btn_simple', T('"Simple words" explains everything in plain English. Switch it off to see technical details.')),
        ('btn_audience', T('Developer or Player: players get a short, spoiler-light list of broken scenes and a bug report for the developer. Developers get the fixing tools.')),
        ('tree', T('All problems found. Red is broken, yellow is probably broken, blue is worth checking. Click one to see what to do.')),
        ('text', T('Here you see what is wrong, what players will notice and exactly how to fix it - with the line before and after.')),
        ('btn_wizard', T('The Fix wizard takes you through the problems one at a time, and checks each fix for you.')),
        ('btn_playtest', T('Play-test plays every scene in an invisible browser and finds errors that only show while playing.')),
        ('btn_tools', T('Tools: back up the game, test a line before you change it, compare two versions, write a bug report, watch for changes.')),
        ('btn_help', T('Help: lessons, a word list, this tour, and comfort settings like extra-large text.')),
        ('health_label', T('The code health score: 100% excellent, 90% great, 80% good, 70% fine.')),
    ]
