"""Simple words: plain-language versions of the scanner's findings for people who
don't write code. Each rule matches an issue title and returns:

  title   short everyday-words name for the list
  what    what is broken
  effect  what a player will notice
  fix     what to change (the exact change is shown separately as before/after)

Every text goes through T() so it can be translated.
"""
import os
import re

from i18n import T
import scanner

_SEV = {scanner.ERROR: 'Broken', scanner.WARNING: 'Probably broken', scanner.INFO: 'Worth checking'}
_MEANING = {
    scanner.ERROR: 'This will go wrong for players.',
    scanner.WARNING: 'This is very likely a mistake.',
    scanner.INFO: 'Not urgent. It may be fine, but have a look.',
}
_CATEGORY = {
    'Broken links': 'Links to missing scenes', 'Missing files': 'Missing pictures and sounds',
    'Macro tags': 'Broken game commands', 'Unknown macros': 'Misspelled game commands',
    'Conditions': '"If" checks that go wrong', 'Code': 'Commands that do nothing or break',
    'Variables': 'Misspelled stats and values', 'Widgets': 'Reusable blocks', 'HTML': 'Page layout code',
    'CSS': 'Styles (colours and looks)', 'Passages': 'Scenes', 'Unused passages': 'Scenes nobody can reach',
    'Unused files': 'Files the game never uses', 'Unfinished content': 'Unfinished text', 'Spelling': 'Spelling',
    'Text': 'Typing slips', 'Portability': 'Works only on Windows', 'Large files': 'Very big files',
    'Broken files': 'Damaged files', 'Outdated code': 'Old-style commands', 'JavaScript': 'Script code',
    'Story setup': 'Game setup', 'Scanner': 'Scanner', 'Play-test': 'Errors while playing',
}


def severity(sev):
    return T(_SEV[sev])


def severity_meaning(sev):
    return T(_MEANING[sev])


def _settled_fine(i):
    return (getattr(i, 'settle', None) or {}).get('state') == 'fine'


def label(i):
    """The word for the list: a result Game Doctor checked and found fine (settle.py) is "Fine"."""
    return T('Fine') if _settled_fine(i) else severity(i.severity)


def meaning_of(i):
    if _settled_fine(i):
        return T('Game Doctor checked this: nothing is wrong.')
    return severity_meaning(i.severity)


def category(name):
    return T(_CATEGORY.get(name, name))


RULES = []


def rule(pattern):
    def deco(fn):
        RULES.append((re.compile(pattern), fn))
        return fn
    return deco


def _scene(i):
    return T('the scene "{name}"', name=i.passage) if i.passage else T('the game')


def _file(path):
    return path.replace('\\', '/').rsplit('/', 1)[-1]


def _near(text, pattern):
    m = re.search(pattern, text)
    return m.group(1) if m else None


# ---------------------------------------------------------------------- links and scenes
@rule(r'^(?:Link|Image link|<<\w+>>|data-passage link|Harlowe link|Script link|Chapbook link) to missing passage "(.*)"$')
def _(m, i):
    return dict(title=T('Goes to a scene that doesn\'t exist: "{name}"', name=m.group(1)),
                what=T('In {where}, a link or a command tries to open a scene called "{name}". No scene in the game has exactly that name.',
                       where=_scene(i), name=m.group(1)),
                effect=T('When the player clicks it (or the game reaches it), they get an error message or a blank page instead of the scene.'),
                fix=T('Make the name match an existing scene exactly. Capital letters and spaces count.'))


@rule(r'^(Jump|Call) to missing label "(.*)"$|^Divert to missing knot/stitch "(.*)"$|^\*(?:goto|gosub) to missing label "(.*)"$')
def _(m, i):
    name = m.group(2) or m.group(3) or m.group(4)
    return dict(title=T('Goes to a part of the story that doesn\'t exist: "{name}"', name=name),
                what=T('The story tries to continue at "{name}", but nothing in the game has that name.', name=name),
                effect=T('The game stops with an error at this point (or will not start at all).'),
                fix=T('Make the name match an existing part of the story exactly.'))


@rule(r'^\*\w+ to missing (?:scene|label) "(.+?)"')
def _(m, i):
    return dict(title=T('Goes to a chapter that doesn\'t exist: "{name}"', name=m.group(1)),
                what=T('The story tries to open "{name}", but there is no such chapter or spot.', name=m.group(1)),
                effect=T('The game stops with an error at this point.'), fix=T('Fix the name so it matches an existing chapter.'))


@rule(r'^Screen "(.+)" does not exist$')
def _(m, i):
    return dict(title=T('Opens a screen that doesn\'t exist: "{name}"', name=m.group(1)),
                what=T('The game tries to show a screen called "{name}", but no screen has that name.', name=m.group(1)),
                effect=T('The game stops with an error here.'), fix=T('Fix the name so it matches an existing screen.'))


@rule(r'^Transfer to missing map|^Call to missing common event')
def _(m, i):
    return dict(title=T('An event sends the player somewhere that doesn\'t exist'),
                what=T('An event moves the player to a map, or runs a common event, that is not in the game.'),
                effect=T('The game crashes or freezes when this event runs.'), fix=T('Pick an existing map or common event in that event command.'))


@rule(r'has an empty target$')
def _(m, i):
    return dict(title=T('A link that goes nowhere'), what=T('A link in {where} does not say which scene it should open.', where=_scene(i)),
                effect=T('Clicking it shows an error.'), fix=T('Write the name of the scene the link should open.'))


@rule(r'^Link is not closed')
def _(m, i):
    return dict(title=T('A link is missing its ending'), what=T('A link starts with [[ but never ends with ]].'),
                effect=T('The text after it gets mixed into the link and the scene looks broken.'), fix=T('Add ]] at the end of the link.'))


@rule(r'^(?:Passage|Label|Knot) "(.*)" is never (?:linked or included|jumped to or called|diverted to)$')
def _(m, i):
    return dict(title=T('Nobody can reach "{name}"', name=m.group(1)), what=T('Nothing in the game leads to this part of the story.'),
                effect=T('Players never see it. Harmless, unless it was supposed to be playable.'),
                fix=T('If players should see it, add a link to it from another scene. If it is old, you can ignore it.'))


@rule(r'^Passage "(.*)" is only linked inside a hidden note$')
def _(m, i):
    src = re.search(r'is in "(.*)" \(line (\d+)\)', i.what)
    where, line = (src.group(1), src.group(2)) if src else ('?', '?')
    return dict(title=T('Nobody can reach "{name}": its link is switched off', name=m.group(1)),
                what=T('The only link to this scene is in "{where}" (line {line}), inside a hidden note /* ... */. '
                       'Everything between /* and */ is switched off.', where=where, line=line),
                effect=T('Players never see this scene.'),
                fix=T('If players should see it, delete the /* and the */ around the link. If you switched it off on purpose, leave it.'))


@rule(r'^Passage "(.*)" is only linked from scenes players can\'t reach$')
def _(m, i):
    return dict(title=T('Nobody can reach "{name}"', name=m.group(1)),
                what=T('The only links to this scene are in other scenes that nobody can reach.'),
                effect=T('Players never see it. Harmless, unless it was supposed to be playable.'),
                fix=T('If players should see it, add a link to it from a scene players can reach. If it is old, you can ignore it.'))


@rule(r'^Passage "(.*)" is empty$')
def _(m, i):
    return dict(title=T('The scene "{name}" is empty', name=m.group(1)), what=T('This scene has no text or content.'),
                effect=T('A player who gets here sees a blank page.'), fix=T('Write the scene, or remove the links that lead to it.'))


@rule(r'^(?:Passage name used (\d+) times|Label "(.+)" is defined twice|Knot "(.+)" is defined twice)')
def _(m, i):
    return dict(title=T('Two parts of the story have the same name'),
                what=T('More than one scene or label uses the name "{name}".', name=i.passage or m.group(2) or m.group(3) or ''),
                effect=T('Only one of them can ever be shown, or the game refuses to start.'), fix=T('Give each one its own name.'))


@rule(r'^Start passage is missing')
def _(m, i):
    return dict(title=T('The game has no starting scene'), what=T('The game does not know where to begin.'),
                effect=T('The game may not start at all.'), fix=T('In Twine, mark the first scene as the start.'))


@rule(r'^Link to missing page anchor')
def _(m, i):
    return dict(title=T('A jump link goes nowhere'), what=T('A link points to a spot on the page that does not exist.'),
                effect=T('Clicking it does nothing.'), fix=T('Fix the name of the spot, or remove the link.'))


# ---------------------------------------------------------------------- files
_KIND = {'Image': 'picture', 'Audio': 'sound', 'Video': 'video', 'Font': 'font', 'Page': 'page', 'File': 'file'}


def _kind(word):
    return T(_KIND.get(word, 'file'))


@rule(r'^(\w+) not found: (.*)$')
def _(m, i):
    kind = _kind(m.group(1))
    return dict(title=T('Missing {kind}: {file}', kind=kind, file=_file(m.group(2))),
                what=T('The game wants to use the {kind} "{path}", but there is no such file in the game folder.', kind=kind, path=m.group(2)),
                effect=T('Players see a broken picture or empty space, or hear nothing.'),
                fix=(T('The name looks mistyped: the file meant is probably "{real}". Check it, and if it is the right file, '
                       'correct the name in the game yourself. If not, put the missing file in the game folder.',
                       real=_near(i.what, r'file meant is probably "(.+?)"'))
                     if _near(i.what, r'file meant is probably "(.+?)"') else
                     T('Put the file in the game folder with exactly this name. Only change the name in the game if you know '
                       'which file was meant: files with similar names are usually different pictures (another character, '
                       'another scene, or one of several random versions).')))


@rule(r'^(\w+) path has wrong capital letters: (.*)$')
def _(m, i):
    return dict(title=T('File name has wrong capital letters: {file}', file=_file(m.group(2))),
                what=T('The file exists, but the game writes its name with different capital letters.'),
                effect=T('Works on Windows. On phones, Macs, Linux or a website the picture or sound will be missing.'),
                fix=T('Change the name in the game file so the capital letters match the real file.'))


@rule(r'uses a full path from someone')
def _(m, i):
    return dict(title=T('A picture points to the developer\'s own computer'),
                what=T('The file location starts with a drive letter like "D:\\". That folder only exists on the computer that made the game.'),
                effect=T('Players never see this picture.'), fix=T('Change it to a location inside the game folder, like images/characters/name.jpg.'))


@rule(r'use "\\" instead of "/"')
def _(m, i):
    return dict(title=T('File locations use \\ instead of /'), what=T('The game writes folder paths with backslashes (\\).'),
                effect=T('Fine on Windows. On phones, Macs, Linux or a website, pictures and sounds will not load.'),
                fix=T('Only matters if the game should run outside Windows: replace \\ with / inside the picture paths.'))


def _full_list(i):
    return ('\n\n' + i.fix.split(':\n', 1)[1]) if ':\n' in i.fix else ''


@rule(r'^(\d+) file\(s\) in "(.*)" are never used$')
def _(m, i):
    return dict(title=T('{n} files in "{folder}" are never used', n=m.group(1), folder=m.group(2)),
                what=T('These files are in the game folder, but the game never uses them.'),
                effect=T('Nothing breaks. They only make the download bigger.'),
                fix=T('Delete them if you are sure they are not needed. (Some may be picked by the game in clever ways the scanner can\'t see.)')
                + _full_list(i))


@rule(r'^(\d+) files in (\d+) folders are never used$')
def _(m, i):
    return dict(title=T('{n} files in {k} folders are never used', n=m.group(1), k=m.group(2)),
                what=T('These files are in the game folder, but the game never uses them.'),
                effect=T('Nothing breaks. They only make the download bigger.'),
                fix=T('Delete them if you are sure they are not needed. (Some may be picked by the game in clever ways the scanner can\'t see.)')
                + _full_list(i))


@rule(r'^Empty file')
def _(m, i):
    return dict(title=T('An empty file'), what=T('This file is empty (0 bytes).'), effect=T('It will not show or play.'),
                fix=T('Replace it with a working copy of the file.'))


@rule(r'^Damaged (\w+) \(cut off\): (.*)$|^Damaged file: (.*)$')
def _(m, i):
    return dict(title=T('Damaged file (cut off): {file}', file=_file(m.group(2) or m.group(3))),
                what=T('The end of this file is missing, usually because a download, copy or unzip did not finish.'),
                effect=T('The picture shows only partly or not at all, or the sound/video stops early.'),
                fix=T('Copy or download this file again from the original game files.'))


@rule(r'^Not a real (\w+): (.*)$')
def _(m, i):
    return dict(title=T('Not really a picture or sound: {file}', file=_file(m.group(2))),
                what=T('The file has the right name, but inside it is a web page (often an error page from a download site).'),
                effect=T('Players see a broken picture or hear nothing.'), fix=T('Download the real file again and replace this one.'))


@rule(r'^Unrecognised or damaged (\w+): (.*)$')
def _(m, i):
    return dict(title=T('Possibly damaged file: {file}', file=_file(m.group(2))),
                what=T('The start of this file does not look like any known picture, sound or video format.'),
                effect=T('It probably will not show or play.'), fix=T('Open it in a picture viewer or media player. Replace it if it does not open.'))


@rule(r'have the wrong file extension$')
def _(m, i):
    return dict(title=T('Some files have the wrong ending (.png, .jpg ...)'),
                what=T('These files contain a different format than their name says, for example a JPEG picture named .png.'),
                effect=T('Browsers usually still show them. Some phones and programs may not.'),
                fix=T('Optional: convert or rename them so the name matches the real format.'))


@rule(r'very large media file')
def _(m, i):
    return dict(title=T('Some files are very big'), what=T('These pictures or videos are very large.'),
                effect=T('They load slowly, especially on phones.'), fix=T('Optional: make them smaller with an image tool.'))


@rule(r'^Map file missing|^Plugin file missing|^Included file not found|in the scene list has no file$')
def _(m, i):
    return dict(title=T('A game file is missing: {name}', name=re.sub(r'^.*?: |^Scene "(.+?)".*$', r'\1', i.title)),
                what=T('The game expects a file that is not there.'),
                effect=T('The game shows an error at start or when this part is reached.'), fix=T('Put the missing file back, or remove the reference to it.'))


@rule(r'^Damaged data file|^File cannot be read')
def _(m, i):
    return dict(title=T('A game data file is damaged'), what=T('This file cannot be read properly.'),
                effect=T('The game will not start or crashes.'), fix=T('Restore the file from a backup or the original game.'))


# ---------------------------------------------------------------------- game commands (tags/macros)
@rule(r'^<<(\w[\w-]*)>> is never closed$|^\((\w[\w-]*):\) is never closed')
def _(m, i):
    name = m.group(1) or m.group(2)
    return dict(title=T('A "{name}" command is never closed', name=name),
                what=T('Some game commands need a matching end. This one has none.'),
                effect=T('The game shows a red error box in this scene, and part of the scene may be missing.'),
                fix=T('Add the missing end (for example <</{name}>>) where this part should end.', name=name))


@rule(r'^<</(\w[\w-]*)>> has no opening|^<</(\w[\w-]*)>> closes a macro')
def _(m, i):
    name = m.group(1) or m.group(2)
    return dict(title=T('An extra end tag <</{name}>>', name=name), what=T('There is an end tag with nothing for it to end.'),
                effect=T('The game shows a red error box in this scene.'), fix=T('Delete this extra end tag.'))


@rule(r'^<<(\w+)>> is outside its <<(.+)>>$')
def _(m, i):
    return dict(title=T('"{name}" is in the wrong place', name=m.group(1)),
                what=T('<<{name}>> must sit inside an <<if>> block. Here it does not, usually because an end tag above it is in the wrong spot.', name=m.group(1)),
                effect=T('The game shows a red error box.'), fix=T('Check the lines just above and move the end tag to the right place.'))


@rule(r'^Closing tag <</(.*)> is missing a ">"$')
def _(m, i):
    return dict(title=T('An end tag is missing a ">": <</{name}>', name=m.group(1)),
                what=T('The end tag is written with one ">" but needs two.'),
                effect=T('The game doesn\'t see the end tag. It shows a red error box and the scene may look wrong.'),
                fix=T('Add one ">" so it ends with ">>".'))


@rule(r'^Extra ">" after a macro tag$|^Stray ">" after an HTML tag$')
def _(m, i):
    return dict(title=T('An extra ">" sign'), what=T('There is one ">" too many after a tag.'),
                effect=T('Players see a stray ">" character on screen.'), fix=T('Delete the extra ">".'))


@rule(r'^Extra "<" before a macro tag$')
def _(m, i):
    return dict(title=T('An extra "<" sign'), what=T('There is one "<" too many before a command.'),
                effect=T('Players see a stray "<" on screen.'), fix=T('Delete the extra "<".'))


@rule(r'^Stray "<<"')
def _(m, i):
    return dict(title=T('A half-written command "<<"'), what=T('There is a "<<" with nothing after it.'),
                effect=T('The command after it breaks and the game may show an error.'), fix=T('Delete the stray "<<".'))


@rule(r'is not closed with ">>"$')
def _(m, i):
    return dict(title=T('A command is missing its ">>"'), what=T('A command starts with << but never ends with >>.'),
                effect=T('Text after it disappears or the scene shows an error.'), fix=T('Add ">>" at the end of the command.'))


@rule(r'^Hook "\[" is never closed|^Insert "\{" is never closed|^"\{" is never closed|^Script tags do not match')
def _(m, i):
    return dict(title=T('A bracket is opened but never closed'), what=T('Something starts with a bracket but its closing bracket is missing.'),
                effect=T('Everything after it is swallowed or the game shows an error.'), fix=T('Add the missing closing bracket.'))


@rule(r'^Hook closed with|^Insert closed with|^Extra "\}"|Curly brackets')
def _(m, i):
    return dict(title=T('An extra closing bracket'), what=T('There is a closing bracket with no opening one.'),
                effect=T('Players may see a stray character or an error.'), fix=T('Remove the extra bracket.'))


@rule(r'has no hook \[ \] after it$')
def _(m, i):
    return dict(title=T('A command has no text attached to it'),
                what=T('This command should control the text right after it, written in square brackets [like this]. Nothing follows it.'),
                effect=T('Harlowe shows an error, or the text is always shown.'), fix=T('Put the text it controls in square brackets right after it.'))


@rule(r'^Unknown (?:macro|command|insert|modifier) (.+)$')
def _(m, i):
    near = _near(i.what, r'very close to (\S+?)\.?$') or _near(i.what, r'Did you mean "?(.+?)"?\?')
    return dict(title=T('Misspelled command: {name}', name=m.group(1)) if near else T('Unknown command: {name}', name=m.group(1)),
                what=(T('The game doesn\'t know this command. It is almost certainly a typo of {near}.', near=near) if near
                      else T('The game doesn\'t know this command.')),
                effect=T('The game shows an error, and the part of the scene it controls does not work.'),
                fix=(T('Change it to {near}.', near=near) if near else T('Check the spelling of the command.')))


@rule(r'is an old SugarCube macro$|is old SugarCube 1 syntax$')
def _(m, i):
    return dict(title=T('An old-style command'), what=T('This command still works, but is outdated.'),
                effect=T('Nothing now. It may break in a future engine update.'), fix=T('Optional: replace it with the newer command.'))


# ---------------------------------------------------------------------- conditions and code
@rule(r'^Single "=" inside a condition$')
def _(m, i):
    return dict(title=T('An "if" check uses "=" instead of "is"'),
                what=T('Inside an "if" check, "=" means "make it equal", not "is it equal?".'),
                effect=T('The game shows a red error box, or the check is always true and the wrong scene part plays.'),
                fix=T('Replace the single "=" with "is".'))


@rule(r'^Wrong comparison operator')
def _(m, i):
    return dict(title=T('"=>" or "=<" written backwards'), what=T('Greater-or-equal is written ">=", not "=>".'),
                effect=T('The check does not work.'), fix=T('Write ">=" or "<=" instead.'))


@rule(r'^"is not" instead of "isnot"$')
def _(m, i):
    return dict(title=T('"is not" should be one word'), what=T('In this game engine "is not" means something different from "isnot".'),
                effect=T('The check is almost always wrong.'), fix=T('Write "isnot" as one word.'))


@rule(r'does not compare both$')
def _(m, i):
    return dict(title=T('An "or" check that is always true'),
                what=T('Something like "$x is 1 or 2" only checks $x against 1; the "2" on its own always counts as yes.'),
                effect=T('This part plays even when it shouldn\'t.'), fix=T('Repeat the name: "$x is 1 or $x is 2".'))


@rule(r'^Condition mixes "and" and "or"')
def _(m, i):
    return dict(title=T('An "if" check that can trigger at the wrong time'),
                what=T('This check mixes "and" and "or" without brackets. The computer always does the "and" parts first, so the check may not mean what was intended.'),
                effect=T('A scene or option can appear when it shouldn\'t - for example when a character is kidnapped or a stat is too low.'),
                fix=T('Add brackets ( ) around the parts that belong together. Look at the code below and group the "or" choices.'))


@rule(r'^Brackets do not match')
def _(m, i):
    return dict(title=T('Brackets ( ) don\'t match'), what=T('There are more "(" than ")" or the other way round.'),
                effect=T('The game shows a red error box.'), fix=T('Add or remove a bracket so they pair up.'))


@rule(r'has no condition$')
def _(m, i):
    return dict(title=T('An empty "if" check'), what=T('The "if" has nothing to check.'), effect=T('The game shows an error.'),
                fix=T('Write what should be checked.'))


@rule(r'^<<set>> compares instead of setting$')
def _(m, i):
    return dict(title=T('A stat change that does nothing ("==")'),
                what=T('This line is meant to change a value, but "==" only compares - it does not change anything.'),
                effect=T('The value never changes. Whatever depends on it (timers, counters) stays stuck.'),
                fix=T('Replace "==" with "to".'))


@rule(r'^<<set>> does not change anything$')
def _(m, i):
    return dict(title=T('A stat change that does nothing'),
                what=T('This line is meant to change a value, but it has no "to", "=", "+=" or "-=", so nothing changes.'),
                effect=T('For example, energy or money is never taken or given.'), fix=T('Write "-=" instead of "-" (or "+=" instead of "+").'))


@rule(r'^<<set>> with nothing to set$')
def _(m, i):
    return dict(title=T('An empty stat change'), what=T('The command is empty.'), effect=T('The game shows an error.'),
                fix=T('Delete it or write the change.'))


@rule(r'uses "=" instead of "to"$')
def _(m, i):
    return dict(title=T('A value is set with "=" instead of "to"'), what=T('This engine sets values with "to".'),
                effect=T('The game shows an error.'), fix=T('Replace "=" with "to".'))


@rule(r'^Unclosed quote in')
def _(m, i):
    return dict(title=T('A missing quote mark "'), what=T('A piece of text in a command opens with " but never closes.'),
                effect=T('The game shows an error or swallows the text after it.'), fix=T('Add the missing " mark.'))


@rule(r'^Tab character used for indentation|^Tabs and spaces mixed')
def _(m, i):
    return dict(title=T('Tabs used where spaces are needed'), what=T('This engine needs lines to be indented with spaces only.'),
                effect=T('The game will not start, or stops with an error here.'), fix=T('Replace the tab at the start of the line with spaces.'))


@rule(r'line is missing ":" at the end$')
def _(m, i):
    return dict(title=T('A line is missing ":" at the end'), what=T('Lines like "if", "menu" and "else" must end with a colon.'),
                effect=T('The game will not start.'), fix=T('Add ":" at the end of the line.'))


@rule(r'^(?:JavaScript brackets|Script code problem|Plugin .+\.js:)')
def _(m, i):
    return dict(title=T('Broken script code'), what=T('A bracket or quote mark in the script code does not match.'),
                effect=T('The whole script stops working, which can break many features.'), fix=T('Check the brackets and quotes around this line.'))


@rule(r'^Leftover debug code')
def _(m, i):
    return dict(title=T('Leftover test code'), what=T('Code used while testing was left in.'), effect=T('Usually nothing visible.'),
                fix=T('Optional: remove it.'))


# ---------------------------------------------------------------------- variables
@rule(r'is read as ".*" plus text$')
def _(m, i):
    return dict(title=T('A name with a "-" in it'), what=T('Names of values can\'t contain "-". The game reads only the part before the "-".'),
                effect=T('Players see the wrong word or a blank instead of a name.'), fix=T('Use "_" instead of "-".'))


@rule(r'^\$?([\w.]+) is used but never set$')
def _(m, i):
    near = _near(i.what, r'misspelling of \$?(\w+)') or _near(i.what, r'Did you mean (?:s\.)?(\w+)\?')
    name = m.group(1)
    return dict(title=T('Misspelled value name: {name}', name=name) if near else T('A value that never gets set: {name}', name=name),
                what=(T('The game uses "{name}", but everywhere else it is called "{near}". It\'s a typo.', name=name, near=near) if near
                      else T('The game checks or shows "{name}", but nothing ever gives it a value.', name=name)),
                effect=T('Checks on it are always "no", so some text or scenes never appear (or a blank shows instead of a name).'),
                fix=(T('Change "{name}" to "{near}".', name=name, near=near) if near else T('Give it a starting value, or fix the name.')))


@rule(r'^(\d+) values are set but never checked or shown$')
def _(m, i):
    return dict(title=T('{n} values the game never uses', n=m.group(1)),
                what=T('These values get changed, but the game never looks at them.'),
                effect=T('Nothing breaks. They may be left over from older versions.'),
                fix=T('Nothing needed, unless one was meant to be used.') + _full_list(i))


@rule(r'^\$(\w+) is set but never checked or shown$')
def _(m, i):
    near = _near(i.what, r'reads \$(\w+)')
    name = '$' + m.group(1)
    return dict(title=T('Misspelled value name: {name}', name=name) if near else T('A value the game never uses: {name}', name=name),
                what=(T('This changes "{name}", but the game only ever looks at "${near}". It\'s a typo.', name=name, near=near) if near
                      else T('"{name}" gets changed, but the game never looks at it.', name=name)),
                effect=(T('The change is lost - for example a stat increase that never counts.') if near
                        else T('Nothing breaks. It may be left over from older versions.')),
                fix=(T('Change "{name}" to "${near}".', name=name, near=near) if near else T('Nothing needed, unless it was meant to be used.')))


@rule(r'^\$(\w+) and \$(\w+) differ only in capital letters$')
def _(m, i):
    return dict(title=T('Same name, different capital letters: ${a} / ${b}', a=m.group(1), b=m.group(2)),
                what=T('To the game these are two different values, because capital letters count.'),
                effect=T('Changes to one are not seen by the other, so stats or scenes go wrong.'),
                fix=T('Change ${a} to ${b}.', a=m.group(1), b=m.group(2)))


@rule(r'^"?\$?(\w+)"? looks like a (?:typo|misspelling) of "?\$?(\w+)"?$')
def _(m, i):
    return dict(title=T('Misspelled value name: {name}', name=m.group(1)),
                what=T('"{name}" is never set up, but "{near}" is. It is almost certainly a typo.', name=m.group(1), near=m.group(2)),
                effect=T('The game shows an error or the value is always empty.'), fix=T('Change "{name}" to "{near}".', name=m.group(1), near=m.group(2)))


@rule(r'^Temporary variable _(\w+) is used but never set$')
def _(m, i):
    return dict(title=T('A temporary value that is never set: _{name}', name=m.group(1)),
                what=T('This short-lived value is used but never gets a value.'), effect=T('Checks on it never work.'),
                fix=T('Set it first, or fix the name.'))


@rule(r'^(?:\*set on undeclared variable|Assignment to undeclared variable) "(\w+)"$|^\$\{(\w+)\} is not a declared variable$')
def _(m, i):
    name = m.group(1) or m.group(2)
    near = _near(i.what, r'Did you mean "(\w+)"\?')
    return dict(title=T('A value that was never created: {name}', name=name),
                what=(T('"{name}" was never created. It is probably a typo of "{near}".', name=name, near=near) if near
                      else T('"{name}" was never created at the start of the game.', name=name)),
                effect=T('The game stops with an error here.'),
                fix=(T('Change "{name}" to "{near}".', name=name, near=near) if near else T('Create it at the start of the game.')))


@rule(r'^(Switch|Variable) #(\d+) does not exist$')
def _(m, i):
    return dict(title=T('Uses a game switch/variable that doesn\'t exist (#{n})', n=m.group(2)),
                what=T('The event uses number {n}, but the game\'s list is shorter.', n=m.group(2)),
                effect=T('The event does not work as intended.'), fix=T('Pick an existing switch/variable in the event.'))


@rule(r'^(Switch|Variable) #(\d+) has no name$')
def _(m, i):
    return dict(title=T('A game switch/variable has no name (#{n})', n=m.group(2)),
                what=T('It is used, but has no name in the Database.'), effect=T('Nothing visible. It is just easy to mix up.'),
                fix=T('Give it a name in the Database.'))


@rule(r'^Widget "(.*)" is defined twice$')
def _(m, i):
    return dict(title=T('A reusable block is defined twice: {name}', name=m.group(1)), what=T('Two definitions share this name.'),
                effect=T('Only one version is used.'), fix=T('Delete or rename one of them.'))


@rule(r'without the "widget" tag$')
def _(m, i):
    return dict(title=T('A reusable block will not load'), what=T('Its scene is missing the "widget" tag.'),
                effect=T('Every scene that uses it shows an error.'), fix=T('Add the tag "widget" to that scene in Twine.'))


@rule(r'^Widget <<(.+)>> is never used$')
def _(m, i):
    return dict(title=T('An unused reusable block: {name}', name=m.group(1)), what=T('It is defined but never used.'),
                effect=T('Nothing breaks.'), fix=T('Nothing needed.'))


# ---------------------------------------------------------------------- page code, styles, text
@rule(r'tag has an unclosed quote$')
def _(m, i):
    return dict(title=T('A missing quote mark in page code'), what=T('A quote mark " is missing inside a page tag (often a picture).'),
                effect=T('The picture or element may not show, or the layout breaks.'), fix=T('Add the missing " mark.'))


@rule(r'^<(\w+)> opened more times than closed')
def _(m, i):
    line = _near(i.what, r'on line (\d+)') or '?'
    tag = m.group(1)
    if i.severity == scanner.INFO:
        return dict(title=T('A page box is not closed: <{tag}> on line {line}', tag=tag, line=line),
                    what=T('The <{tag}> on line {line} is never closed with </{tag}>.', tag=tag, line=line),
                    effect=T('Usually nothing: the browser closes it by itself at the end of the scene.'),
                    fix=T('Add </{tag}> where the box should end.', tag=tag))
    return dict(title=T('A page box is not closed: <{tag}> on line {line}', tag=tag, line=line),
                what=T('The <{tag}> on line {line} is never closed with </{tag}>.', tag=tag, line=line),
                effect=T('Text or pictures may end up in the wrong place or with the wrong style.'),
                fix=T('Add </{tag}> where the box should end.', tag=tag))


@rule(r'^<(\w+)> closed more times than opened')
def _(m, i):
    line = _near(i.what, r'on line (\d+)') or '?'
    return dict(title=T('An extra closing tag: </{tag}> on line {line}', tag=m.group(1), line=line),
                what=T('The </{tag}> on line {line} closes a box that was never opened.', tag=m.group(1), line=line),
                effect=T('Text or pictures may end up in the wrong place or with the wrong style.'),
                fix=T('Delete the extra </{tag}>, or add the missing <{tag}>.', tag=m.group(1)))


@rule(r'opened more times than closed|closed more times than opened')
def _(m, i):
    return dict(title=T('Page boxes that are not closed properly'),
                what=T('A layout box (like <div> or <center>) is opened but not closed, or closed too often.'),
                effect=T('Text or pictures may end up in the wrong place or with the wrong style.'), fix=T('Add the missing closing tag, or remove the extra one.'))


@rule(r'^id=".*" used \d+ times')
def _(m, i):
    return dict(title=T('Two things share the same name tag (id)'), what=T('Each id must be unique on the page.'),
                effect=T('Buttons that update this part may change the wrong spot.'), fix=T('Give each one its own id.'))


@rule(r'^Empty (src|href)')
def _(m, i):
    return dict(title=T('A picture or link with no file'), what=T('The tag points at nothing.'), effect=T('A broken picture icon may show.'),
                fix=T('Fill in the file name, or remove the tag.'))


@rule(r'invalid unit')
def _(m, i):
    return dict(title=T('Picture sizes written in a way browsers ignore'),
                what=T('Picture sizes like height="450wv" have letters the browser does not understand ("wv" is not a unit).'),
                effect=T('Pictures still show, just at a fixed pixel size. Usually harmless.'), fix=T('Optional: remove the letters, e.g. height="450".'))


@rule(r'^Class "(.*)" does not match any style')
def _(m, i):
    near = _near(i.title, r'did you mean "(.*)"')
    return dict(title=T('A style name that doesn\'t match: "{name}"', name=m.group(1)),
                what=T('The look of this part is chosen by the name "{name}", but the style is called "{near}".', name=m.group(1), near=near or '?'),
                effect=T('This part shows without its intended colours or layout.'), fix=T('Change "{name}" to "{near}".', name=m.group(1), near=near or '?'))


@rule(r'^Class "(.*)" has no style rule$')
def _(m, i):
    return dict(title=T('A style name with no style: "{name}"', name=m.group(1)), what=T('No style rule uses this name.'),
                effect=T('Usually harmless.'), fix=T('Nothing needed.'))


@rule(r'^"(.+?)" text shown to players$|^Placeholder text shown to players$')
def _(m, i):
    return dict(title=T('Unfinished text players can see'),
                what=T('The developer left a note like "PLACEHOLDER" or "TODO" in the story.'),
                effect=T('Players see this note, or the scene is unfinished.'), fix=T('Write the real text, or remove the note.'))


@rule(r'^"(.+)" → "(.+)" or "(.+)"$')
def _(m, i):
    return dict(title=T('Spelling: "{wrong}" should be "{a}" or "{b}"', wrong=m.group(1), a=m.group(2), b=m.group(3)),
                what=T('A common spelling mistake. It can mean two different words.'), effect=T('Players see the misspelled word.'),
                fix=T('Read the sentence and change "{wrong}" to "{a}" or to "{b}", whichever fits.', wrong=m.group(1), a=m.group(2), b=m.group(3)))


@rule(r'^"(.+)" → "(.+)"$')
def _(m, i):
    return dict(title=T('Spelling: "{wrong}" should be "{right}"', wrong=m.group(1), right=m.group(2)),
                what=T('A common spelling mistake.'), effect=T('Players see the misspelled word.'),
                fix=T('Change "{wrong}" to "{right}".', wrong=m.group(1), right=m.group(2)))


@rule(r'^Repeated word "(.+)"$')
def _(m, i):
    return dict(title=T('Doubled word: "{words}"', words=m.group(1)), what=T('The same word is typed twice in a row.'),
                effect=T('Players see the doubled word.'), fix=T('Delete one of them.'))


@rule(r'^Image "(.+)" is not defined$')
def _(m, i):
    return dict(title=T('Shows a picture that doesn\'t exist: "{name}"', name=m.group(1)),
                what=T('No picture with this name is defined, and no file in images/ matches.'),
                effect=T('Players see a placeholder or an error.'), fix=T('Add the picture, or fix the name.'))


@rule(r'^Story has no IFID')
def _(m, i):
    return dict(title=T('The game has no ID number'), what=T('Twine uses an ID to tell stories apart.'), effect=T('Usually nothing visible.'),
                fix=T('Publish the story again from Twine.'))


# ---------------------------------------------------------------------- play-test
@rule(r'^Error box when the scene is shown: (.+)$')
def _(m, i):
    return dict(title=T('Shows a red error box when played: "{name}"', name=i.passage),
                what=T('When the scene was actually played, the game showed this error: {msg}', msg=i.snippet),
                effect=T('Players see a red error box in this scene, and part of it is missing.'),
                fix=T('Look at the problems listed for this same scene - they usually explain the cause.'))


@rule(r'^Script does not load')
def _(m, i):
    return dict(title=T('A script cannot even be read by the browser'),
                what=T('The browser found a typing error in this script, so none of it runs.'),
                effect=T('Features made by this script (special commands, settings) are missing or broken.'),
                fix=T('Look for a missing bracket, quote or comma on this line or the line before.'))


@rule(r'file\(s\) failed to load while playing$')
def _(m, i):
    return dict(title=T('Pictures or sounds missing while playing'),
                what=T('These files were requested while the scenes were played, but they do not exist.'),
                effect=T('Players see broken pictures or hear nothing in some scenes.'), fix=T('Add the missing files, or fix their names.'))


# ---------------------------------------------------------------------- Full scan (the finding's own text already
# names the values that caused it, in plain words, so it is kept as "what")
@rule(r'^File failed to load while playing: (.+)$')
def _(m, i):
    return dict(title=T('Missing picture or sound, seen while playing: {file}', file=_file(m.group(1))), what=i.what,
                effect=T('Players see a broken picture or hear nothing in this scene.'),
                fix=T('Add the missing file, or fix the name the scene uses.'))


@rule(r'^(?:Error box|\d+ error boxes) when the scene is shown on its own')
def _(m, i):
    return dict(title=T('Maybe a red error box: "{name}"', name=i.passage), what=i.what,
                effect=T('It only showed when this scene was played on its own. In the real game an earlier scene may '
                         'set things up first, so players may never see it.'),
                fix=T('Play to this scene the normal way. If the red box shows up, look at the problems listed for '
                      'this scene.'))


@rule(r'^(?:File failed|\d+ files failed) to load when the scene is shown on its own')
def _(m, i):
    return dict(title=T('Maybe a missing picture or sound: "{name}"', name=i.passage), what=i.what,
                effect=T('It only happened with unusual values, so players may never see it.'),
                fix=T('Play to this scene the normal way and see if a picture or sound is missing.'))


@rule(r'^The scene froze the game while it was tested')
def _(m, i):
    return dict(title=T('The game froze in this scene while testing: "{name}"', name=i.passage), what=i.what,
                effect=T('If it happens in the real game too, the game stops answering here.'),
                fix=T('Play to this scene and see if the game freezes. If it does, look for a loop that never ends.'))


@rule(r'branch\(es\) never ran with any values tried$')
def _(m, i):
    return dict(title=T('Parts of scenes the Full scan could not open'), what=i.what,
                effect=T('Nothing is wrong. Problems inside these parts could only be checked by reading, not by playing.'),
                fix=T('Nothing to do, unless you expect players to see these parts.'))


@rule(r'^Error while the game starts')
def _(m, i):
    return dict(title=T('An error while the game starts'), what=T('The browser reported an error while loading the game.'),
                effect=T('The start screen or some features may be broken.'), fix=T('Look at the game\'s main script for the problem named in the message.'))


PLAY_NOTES = ('Confirmed while playing:', 'Not checked by playing:', 'Worth checking:', 'Checked by Game Doctor:')


def simplify(i):
    """Return dict(title, what, effect, fix) in plain words."""
    for rx, fn in RULES:
        m = rx.search(i.title)
        if m:
            d = fn(m, i)
            break
    else:
        d = dict(title=i.title, what=i.what, effect='', fix=i.fix)
    d.setdefault('effect', '')
    # what the Full scan added to a Quick scan finding (confirmed while playing, never shown, players never see it)
    notes = [p for p in i.what.split('\n\n')[1:] if p.startswith(PLAY_NOTES)]
    if notes and d['what'] != i.what:
        d['what'] = '\n\n'.join([d['what']] + notes)
    if i.unreachable:
        d['effect'] = T('Players never see this: it is in a scene nobody can reach. Safe to leave.')
    return d
