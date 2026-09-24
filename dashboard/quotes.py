"""Daily motivation: real quotes from people the user likes, translated to Norwegian.

Only well-documented quotes are included, with a note on the source in a comment.
Where a famous line is really someone else's summary, the author field says so
(e.g. the "excellence is a habit" line is Will Durant summarising Aristotle).

`quote_of_day(day)` picks one quote per calendar day. It is deterministic: the same date
always gives the same quote, so the page does not change on every refresh.
"""

QUOTES = [
    # Marcus Aurelius – Meditasjoner
    {"text": "Det som står i veien for handlingen, driver handlingen fremover. "
             "Det som står i veien, blir veien.",
     "author": "Marcus Aurelius"},                                   # Meditasjoner 5.20
    # David Goggins – Can't Hurt Me
    {"text": "De viktigste samtalene du noen gang får, er de du har med deg selv.",
     "author": "David Goggins"},
    # Seneca – Brev til Lucilius 13
    {"text": "Vi lider oftere i fantasien enn i virkeligheten.",
     "author": "Seneca"},
    # Arnold Schwarzenegger – Pumping Iron (1977)
    {"text": "Det er de tre–fire siste repetisjonene som får muskelen til å vokse. "
             "Det er smerten der som skiller en mester fra en som ikke er det.",
     "author": "Arnold Schwarzenegger"},
    # Epiktet – Samtaler 2.18
    {"text": "Enhver vane og evne holdes ved like og styrkes av de tilsvarende handlingene: "
             "evnen til å gå ved å gå, og evnen til å løpe ved å løpe.",
     "author": "Epiktet"},
    # Tim Ferriss – The 4-Hour Body
    {"text": "Minste effektive dose er rett og slett den minste dosen som gir ønsket resultat.",
     "author": "Tim Ferriss"},
    # Aristoteles – Den nikomakiske etikk, bok I
    {"text": "Én svale gjør ingen sommer, og heller ikke én dag.",
     "author": "Aristoteles"},
    # Sokrates, gjengitt av Xenofon – Erindringer om Sokrates 3.12
    {"text": "Det er en skam å bli gammel av ren likegyldighet, før man har sett hvor "
             "vakker og sterk kroppen kan bli.",
     "author": "Sokrates (hos Xenofon)"},
    # Marcus Aurelius – Meditasjoner 5.1
    {"text": "Når du har vondt for å komme deg opp om morgenen, si til deg selv: "
             "Jeg står opp for å gjøre et menneskes arbeid.",
     "author": "Marcus Aurelius"},
    # David Goggins – «40 %-regelen»
    {"text": "Når du tror du er ferdig, har du bare brukt rundt 40 prosent av det du har i deg.",
     "author": "David Goggins"},
    # Seneca – Om livets korthet, kap. 1
    {"text": "Det er ikke det at vi har kort tid å leve, men at vi kaster bort mye av den.",
     "author": "Seneca"},
    # Epiktet – Samtaler 1.15
    {"text": "Ingenting stort blir til plutselig, like lite som en drueklase eller en fiken.",
     "author": "Epiktet"},
    # Arnold Schwarzenegger – Pumping Iron (1977)
    {"text": "Grensen går i hodet. Så lenge du kan se for deg at du klarer noe, "
             "så klarer du det – så lenge du tror på det hundre prosent.",
     "author": "Arnold Schwarzenegger"},
    # Tim Ferriss – The 4-Hour Workweek
    {"text": "Det vi frykter mest å gjøre, er som regel det vi mest trenger å gjøre.",
     "author": "Tim Ferriss"},
    # Marcus Aurelius – Meditasjoner 10.16
    {"text": "Slutt å diskutere hvordan et godt menneske skal være. Vær et.",
     "author": "Marcus Aurelius"},
    # Will Durant, The Story of Philosophy (1926), som oppsummerer Aristoteles
    {"text": "Vi er det vi gjentatte ganger gjør. Fortreffelighet er altså ikke en handling, "
             "men en vane.",
     "author": "Will Durant (om Aristoteles)"},
    # Seneca – Brev til Lucilius 1
    {"text": "Mens vi utsetter, raser livet forbi.",
     "author": "Seneca"},
    # David Goggins – Can't Hurt Me
    {"text": "Du står i fare for å leve et liv så komfortabelt og mykt at du dør uten "
             "noen gang å ha oppdaget ditt sanne potensial.",
     "author": "David Goggins"},
    # Epiktet – Håndbok (Encheiridion) 5
    {"text": "Det er ikke tingene i seg selv som uroer oss, men våre meninger om dem.",
     "author": "Epiktet"},
    # Aristoteles – Den nikomakiske etikk, bok II
    {"text": "Det vi må lære før vi kan gjøre det, lærer vi ved å gjøre det.",
     "author": "Aristoteles"},
    # Tim Ferriss – The 4-Hour Workweek
    {"text": "Forholdene er aldri perfekte. «En dag» er en sykdom som tar drømmene dine "
             "med seg i graven.",
     "author": "Tim Ferriss"},
    # Marcus Aurelius – Meditasjoner 7.67
    {"text": "Det skal svært lite til for å leve et lykkelig liv. Alt ligger i deg selv, "
             "i din måte å tenke på.",
     "author": "Marcus Aurelius"},
    # Seneca – Brev til Lucilius 71
    {"text": "Når en mann ikke vet hvilken havn han styrer mot, er ingen vind gunstig.",
     "author": "Seneca"},
    # Sokrates, gjengitt av Platon – Forsvarstalen 38a
    {"text": "Et liv som ikke blir prøvd og undersøkt, er ikke verdt å leve.",
     "author": "Sokrates"},
    # Epiktet – Samtaler 3.23
    {"text": "Si først til deg selv hva du vil være, og gjør så det du må gjøre.",
     "author": "Epiktet"},
    # Aristoteles – Den nikomakiske etikk, bok II
    {"text": "Vi blir rettferdige ved å handle rettferdig, måteholdne ved å vise måtehold "
             "og modige ved å handle modig.",
     "author": "Aristoteles"},
    # Tim Ferriss – The 4-Hour Workweek
    {"text": "Mangel på tid er egentlig mangel på prioriteringer.",
     "author": "Tim Ferriss"},
    # Seneca – Brev til Lucilius 101
    {"text": "Begynn å leve med én gang, og regn hver enkelt dag som et eget liv.",
     "author": "Seneca"},
    # Marcus Aurelius – Meditasjoner 5.16
    {"text": "Slik tankene dine vanligvis er, slik blir også sinnet ditt, "
             "for sjelen farges av tankene.",
     "author": "Marcus Aurelius"},
    # Epiktet – Håndbok (Encheiridion) 51
    {"text": "Hvor lenge vil du vente før du mener at du fortjener det beste?",
     "author": "Epiktet"},
    # Seneca – Brev til Lucilius 76
    {"text": "Så lenge du lever, må du fortsette å lære hvordan man lever.",
     "author": "Seneca"},
]


def quote_of_day(day):
    """Return the quote for a given date as {"text": …, "author": …}.

    The day number since year 1 (`date.toordinal()`) modulo the number of quotes gives
    a stable index, so every date maps to the same quote and consecutive days rotate
    through the whole list.
    """
    try:
        index = day.toordinal() % len(QUOTES)
    except AttributeError:
        index = 0
    return dict(QUOTES[index])
