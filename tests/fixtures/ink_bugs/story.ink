INCLUDE extra.ink
INCLUDE gone.ink
VAR gold = 5

You wake up. {gold} coins.
* [Go] -> town
* [Lost] -> twon

=== town ===
Town. {goldd}
~ goldd = 3
~ silver = 1
-> END

=== lonely ===
Nobody. TODO
-> DONE

=== broken ===
{gold > 1: rich
-> END
