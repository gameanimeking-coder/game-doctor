define e = Character("Eileen")
default gold = 5

label start:
    scene bg room
    show eileen happy
    show eilen sad
    play music "audio/theme.ogg"
    play sound "audio/boom.ogg"
    e "You have [gold] gold. PLACEHOLDER"
    if goldd > 3
        e "Rich"
	jump chapter_two
    jump chapter_too
    call screen inventori

label chapter_two:
    e "Hi"
    return

label chapter_two:
    return

label lonely:
    return

screen inventory():
    text "Items"
