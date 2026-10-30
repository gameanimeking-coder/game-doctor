"""Static reference data used by the scanner: known macros, special passages,
file types and common English misspellings."""

# SugarCube 2 built-in macros
SUGARCUBE_MACROS = {
    'capture', 'set', 'unset', 'run', 'script', 'include', 'nobr', 'print', '=', '-',
    'do', 'silently', 'type', 'if', 'elseif', 'else', 'for', 'break', 'continue',
    'switch', 'case', 'default', 'button', 'checkbox', 'cycle', 'option', 'optionsfrom',
    'link', 'linkappend', 'linkprepend', 'linkreplace', 'listbox', 'numberbox',
    'radiobutton', 'textarea', 'textbox', 'actions', 'back', 'choice', 'return',
    'addclass', 'append', 'copy', 'prepend', 'remove', 'removeclass', 'replace',
    'toggleclass', 'audio', 'cacheaudio', 'createaudiogroup', 'createplaylist',
    'masteraudio', 'playlist', 'removeaudiogroup', 'removeplaylist', 'waitforaudio',
    'track', 'goto', 'repeat', 'stop', 'timed', 'next', 'widget', 'display', 'click',
    'remember', 'forget', 'done', 'redo', 'setplaylist', 'stopallaudio', 'endif',
    'endfor', 'endnobr', 'endsilently', 'endclick', 'endwidget', 'endscript',
}

# Container macros: name -> allowed child tags
SUGARCUBE_CONTAINERS = {
    'capture': set(), 'script': set(), 'nobr': set(), 'silently': set(), 'type': set(),
    'do': set(), 'if': {'elseif', 'else'}, 'for': set(), 'switch': {'case', 'default'},
    'button': set(), 'cycle': {'option', 'optionsfrom'}, 'link': set(),
    'linkappend': set(), 'linkprepend': set(), 'linkreplace': set(),
    'listbox': {'option', 'optionsfrom'}, 'append': set(), 'prepend': set(),
    'replace': set(), 'createaudiogroup': {'track'}, 'createplaylist': {'track'},
    'repeat': set(), 'timed': {'next'}, 'widget': set(), 'done': set(), 'click': set(),
}
CHILD_TAGS = {'elseif': 'if', 'else': 'if', 'case': 'switch', 'default': 'switch',
              'option': 'cycle/listbox', 'optionsfrom': 'cycle/listbox', 'next': 'timed'}

DEPRECATED_MACROS = {
    'click': 'Use <<link>> instead of <<click>>.',
    'display': 'Use <<include>> instead of <<display>>.',
    'remember': 'Use the memorize() / recall() functions instead of <<remember>>.',
    'actions': 'Use normal [[links]] instead of <<actions>>.',
    'choice': 'Use normal [[links]] instead of <<choice>>.',
    'setplaylist': 'Use <<createplaylist>> instead of <<setplaylist>>.',
    'stopallaudio': 'Use <<audio ":all" stop>> instead of <<stopallaudio>>.',
}

SPECIAL_PASSAGES = {
    'StoryInit', 'StoryCaption', 'StoryMenu', 'StoryBanner', 'StorySubtitle', 'StoryAuthor',
    'StoryTitle', 'StoryInterface', 'StoryShare', 'StoryData', 'StoryDisplayTitle',
    'PassageReady', 'PassageDone', 'PassageHeader', 'PassageFooter', 'StoryIncludes',
    'StorySettings', 'MenuOptions', 'MenuShare', 'MenuStory', 'Start',
}
SPECIAL_TAGS = {'widget', 'script', 'stylesheet', 'init', 'startup', 'header', 'footer',
                'debug-header', 'debug-footer', 'debug-startup', 'Twine.private',
                'Twine.image', 'Twine.audio', 'Twine.video', 'Twine.vtt', 'annotation'}

IMAGE_EXT = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg', 'ico', 'avif', 'jfif', 'tif', 'tiff'}
AUDIO_EXT = {'mp3', 'ogg', 'oga', 'wav', 'm4a', 'aac', 'flac', 'opus', 'weba'}
VIDEO_EXT = {'mp4', 'webm', 'ogv', 'mov', 'm4v', 'mkv', 'avi'}
FONT_EXT = {'ttf', 'otf', 'woff', 'woff2', 'eot'}
CODE_EXT = {'css', 'js', 'json', 'vtt', 'srt'}
PAGE_EXT = {'html', 'htm'}
MEDIA_EXT = IMAGE_EXT | AUDIO_EXT | VIDEO_EXT | FONT_EXT
ALL_ASSET_EXT = MEDIA_EXT | CODE_EXT | PAGE_EXT

def asset_kind(ext):
    ext = ext.lower()
    if ext in IMAGE_EXT: return 'Image'
    if ext in AUDIO_EXT: return 'Audio'
    if ext in VIDEO_EXT: return 'Video'
    if ext in FONT_EXT: return 'Font'
    if ext in PAGE_EXT: return 'Page'
    return 'File'

# Words that are normal to repeat ("had had", "no no no", moaning...)
REPEAT_OK = {
    'had', 'that', 'no', 'yes', 'oh', 'ah', 'ha', 'haha', 'hm', 'mm', 'very', 'so', 'go',
    'more', 'please', 'now', 'come', 'bye', 'really', 'faster', 'harder', 'deeper', 'yeah',
    'wait', 'stop', 'fuck', 'god', 'is', 'there', 'run', 'hey', 'help', 'okay', 'ok', 'well',
    'again', 'right', 'quick', 'yea', 'nah', 'mmm', 'ooh', 'aah', 'ahh', 'ohh', 'uh', 'um',
    'die', 'kill', 'hurry', 'out', 'up', 'down', 'harder', 'slow', 'boom', 'knock', 'tick',
    'tock', 'blah', 'la', 'na', 'ho', 'he', 'hee', 'ugh', 'yes!', 'damn', 'shit', 'cum',
    'love', 'deep', 'big', 'good', 'bad', 'step', 'little', 'many', 'far', 'long', 'over',
}

# Small words that are almost never doubled on purpose ("for for", "the the")
REPEAT_CHECK = {
    'the', 'a', 'an', 'to', 'of', 'in', 'for', 'and', 'is', 'on', 'at', 'with', 'was', 'are',
    'be', 'but', 'or', 'it', 'its', "it's", 'from', 'this', 'by', 'as', 'will', 'can', 'have',
    'has', 'your', 'his', 'her', 'their', 'our', 'me', 'him', 'them', 'into', 'onto',
    'than', 'then', 'when', 'what', 'if', 'not', 'do', 'does', 'did', 'were', 'been', 'i',
}

# Common English misspellings -> correction
MISSPELLINGS = {
    'abscence': 'absence', 'accomodate': 'accommodate', 'acheive': 'achieve',
    'accross': 'across', 'agressive': 'aggressive', 'alot': 'a lot', 'allready': 'already',
    'amatuer': 'amateur', 'apparantly': 'apparently', 'appearence': 'appearance',
    'arguement': 'argument', 'assasin': 'assassin', 'basicly': 'basically',
    'becuase': 'because', 'beacuse': 'because', 'becasue': 'because', 'begining': 'beginning',
    'beleive': 'believe', 'belive': 'believe', 'bizzare': 'bizarre', 'buisness': 'business',
    'calender': 'calendar', 'cathatonic': 'catatonic', 'catagory': 'category',
    'cemetary': 'cemetery', 'collegue': 'colleague', 'comming': 'coming',
    'commited': 'committed', 'completly': 'completely', 'concious': 'conscious',
    'curiousity': 'curiosity', 'definately': 'definitely', 'definatly': 'definitely',
    'definetly': 'definitely', 'desparate': 'desperate', 'diffrent': 'different',
    'dissapear': 'disappear', 'dissapoint': 'disappoint', 'embarass': 'embarrass',
    'embarassed': 'embarrassed', 'enviroment': 'environment', 'excercise': 'exercise',
    'existance': 'existence', 'experiance': 'experience', 'familar': 'familiar',
    'finaly': 'finally', 'foriegn': 'foreign', 'freind': 'friend', 'freinds': 'friends',
    'fourty': 'forty', 'gaurd': 'guard', 'goverment': 'government', 'grammer': 'grammar',
    'happend': 'happened', 'harrass': 'harass', 'havent': "haven't", 'heighth': 'height',
    'hieght': 'height', 'humourous': 'humorous', 'immediatly': 'immediately',
    'imediately': 'immediately', 'independant': 'independent', 'intresting': 'interesting',
    'interupt': 'interrupt', 'irresistable': 'irresistible', 'jist': 'gist',
    'knowlege': 'knowledge', 'liason': 'liaison', 'libary': 'library', 'lisence': 'licence',
    'lightening': 'lightning', 'maintainance': 'maintenance', 'medecine': 'medicine',
    'millenium': 'millennium', 'mischievious': 'mischievous', 'mispell': 'misspell',
    'neccessary': 'necessary', 'necesary': 'necessary', 'neice': 'niece',
    'noticable': 'noticeable', 'occassion': 'occasion', 'occured': 'occurred',
    'occurence': 'occurrence', 'ocurred': 'occurred', 'oppertunity': 'opportunity',
    'oportunity': 'opportunity', 'persistant': 'persistent', 'posession': 'possession',
    'possibile': 'possible', 'possibilty': 'possibility', 'prefered': 'preferred',
    'probaly': 'probably', 'probobly': 'probably', 'proffesional': 'professional',
    'promiss': 'promise', 'pronounciation': 'pronunciation', 'publically': 'publicly',
    'quikly': 'quickly', 'quickely': 'quickly', 'realy': 'really', 'reccomend': 'recommend',
    'recomend': 'recommend', 'recieve': 'receive', 'recieved': 'received',
    'relevent': 'relevant', 'religous': 'religious', 'remeber': 'remember',
    'resistence': 'resistance', 'responsability': 'responsibility', 'rythm': 'rhythm',
    'saftey': 'safety', 'scissor': 'scissors', 'secratary': 'secretary', 'seige': 'siege',
    'sentance': 'sentence', 'seperate': 'separate', 'seperated': 'separated',
    'sieze': 'seize', 'similiar': 'similar', 'sincerly': 'sincerely', 'speach': 'speech',
    'strenght': 'strength', 'strenth': 'strength', 'succesful': 'successful',
    'successfull': 'successful', 'supercede': 'supersede', 'suprise': 'surprise',
    'suprised': 'surprised', 'surprize': 'surprise', 'tatoo': 'tattoo', 'tendancy': 'tendency',
    'therfore': 'therefore', 'threshhold': 'threshold', 'thier': 'their', 'tommorow': 'tomorrow',
    'tomorow': 'tomorrow', 'tounge': 'tongue', 'truely': 'truly', 'twelth': 'twelfth',
    'tyrany': 'tyranny', 'underate': 'underrate', 'untill': 'until', 'unfortunatly': 'unfortunately',
    'useing': 'using', 'vaccum': 'vacuum', 'vegtable': 'vegetable', 'visable': 'visible',
    'wierd': 'weird', 'wich': 'which', 'whith': 'with', 'withing': 'within', 'wont': "won't",
    'writting': 'writing', 'youre': "you're", 'yatch': 'yacht', 'teh': 'the', 'hte': 'the',
    'adn': 'and', 'nad': 'and', 'taht': 'that', 'waht': 'what', 'whta': 'what', 'thta': 'that',
    'jsut': 'just', 'knwo': 'know', 'konw': 'know', 'woudl': 'would', 'wouldnt': "wouldn't",
    'couldnt': "couldn't", 'shouldnt': "shouldn't", 'didnt': "didn't", 'doesnt': "doesn't",
    'isnt': "isn't", 'wasnt': "wasn't", 'arent': "aren't", 'cant': "can't", 'dont': "don't",
    'im': "I'm", 'ive': "I've", 'theyre': "they're", 'thats': "that's", 'whats': "what's",
    'comeone': 'someone', 'somone': 'someone', 'everytime': 'every time', 'infront': 'in front',
    'alright': 'all right', 'aswell': 'as well', 'atleast': 'at least', 'eachother': 'each other',
    'noone': 'no one', 'ofcourse': 'of course', 'incase': 'in case', 'alittle': 'a little',
    'abit': 'a bit', 'loose control': 'lose control', 'shure': 'sure', 'whit': 'with',
    'wether': 'whether (or "weather")', 'begginning': 'beginning', 'comfortabel': 'comfortable',
    'confortable': 'comfortable', 'beutiful': 'beautiful', 'beatiful': 'beautiful',
    'beautifull': 'beautiful', 'carefull': 'careful', 'helpfull': 'helpful',
    'wonderfull': 'wonderful', 'awfull': 'awful', 'usefull': 'useful', 'gratefull': 'grateful',
    'peacefull': 'peaceful', 'powerfull': 'powerful', 'painfull': 'painful',
    'faithfull': 'faithful', 'skillfull': 'skillful', 'thankfull': 'thankful',
    'stoped': 'stopped', 'droped': 'dropped', 'planed': 'planned', 'runing': 'running',
    'geting': 'getting', 'siting': 'sitting', 'puting': 'putting', 'hitted': 'hit',
    'grabed': 'grabbed', 'lable': 'label', 'nipps': 'nips', 'pantys': 'panties',
    'tittys': 'titties', 'pusy': 'pussy', 'orgams': 'orgasm', 'orgasim': 'orgasm',
    'hapy': 'happy', 'sexualy': 'sexually', 'naturaly': 'naturally', 'actualy': 'actually',
    'usualy': 'usually', 'totaly': 'totally', 'exactlty': 'exactly', 'basicaly': 'basically',
    'quitely': 'quietly', 'sudenly': 'suddenly', 'suddently': 'suddenly', 'slowy': 'slowly',
    'emberassed': 'embarrassed', 'rediculous': 'ridiculous', 'ridiculus': 'ridiculous',
    'whoes': 'whose', 'hense': 'hence', 'acident': 'accident', 'accidently': 'accidentally',
    'cheif': 'chief', 'concience': 'conscience', 'decieve': 'deceive', 'feild': 'field',
    'freindly': 'friendly', 'gaurantee': 'guarantee', 'greatful': 'grateful',
    'hipocrite': 'hypocrite', 'ignorence': 'ignorance', 'jewelery': 'jewellery',
    'laughting': 'laughing', 'lenght': 'length', 'mysterius': 'mysterious',
    'nieghbor': 'neighbor', 'nieghbour': 'neighbour', 'neighbor': None,
    'obediance': 'obedience', 'obidient': 'obedient', 'pleasue': 'pleasure',
    'plesure': 'pleasure', 'positon': 'position', 'pregant': 'pregnant',
    'priviledge': 'privilege', 'seduse': 'seduce', 'shoudl': 'should', 'sholud': 'should',
    'sould': 'should', 'stomache': 'stomach', 'stregth': 'strength', 'theif': 'thief',
    'throught': 'through', 'thruogh': 'through', 'togheter': 'together', 'togehter': 'together',
    'tought': 'thought', 'whant': 'want', 'whants': 'wants', 'wating': 'waiting',
    'wellcome': 'welcome', 'whereever': 'wherever', 'yeild': 'yield', 'yuo': 'you',
}
# Entries that are also real words or common informal spellings
for _w in ('wont', 'im', 'alright', 'scissor', 'whit', 'nipps', 'hitted', 'jist', 'cant'):
    MISSPELLINGS.pop(_w, None)
MISSPELLINGS = {k: v for k, v in MISSPELLINGS.items() if v and ' ' not in k}
# Misspellings that can mean two different words: offer both instead of guessing
AMBIGUOUS = {
    'comeone': ('someone', 'come on'), 'wether': ('whether', 'weather'), 'tought': ('thought', 'taught'),
    'throught': ('through', 'thought'), 'whoes': ('whose', "who's"), 'sould': ('should', 'would'),
}
for _w, (_a, _b) in AMBIGUOUS.items():
    MISSPELLINGS[_w] = _a

PLACEHOLDER_WORDS = ['PLACEHOLDER', 'TODO', 'FIXME', 'TBD', 'lorem ipsum',
                     'DA SISTEMARE', 'TEMP TEXT', 'INSERT TEXT', 'ADD TEXT HERE']

# Classes SugarCube and browsers add or style themselves
BUILTIN_CLASSES = {
    'link-internal', 'link-external', 'link-image', 'link-broken', 'link-visited', 'passage',
    'error', 'error-view', 'debug', 'macro', 'container', 'center', 'hidden', 'nobr', 'icon',
    'speech', 'ui-dialog', 'ui-close', 'button', 'active', 'disabled', 'selected',
}
