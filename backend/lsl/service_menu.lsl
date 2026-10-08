# Nabídka služeb a upravitelná ukázková zadání pro terminálový klient.
items = [
    {"id": "auto", "name": "Automaticky podle zadání", "task": "Prověř nákupní košík ukázkového e-shopu. Objednej kompletní HTTP audit se třemi testy co nejlevněji. Při vadné dodávce reklamuj a zkus jiného dodavatele."},
    {"id": "short-research", "name": "Krátká rešerše", "task": "Připrav krátkou rešerši v češtině: co jsou autonomní softwaroví agenti a jak souvisejí s multiagentními systémy? Uveď dva dohledané zdroje."},
    {"id": "python-code", "name": "Python kód", "task": "Napiš Python funkci slugify(text), která vytvoří malá ASCII písmena, převede mezery na pomlčky a odstraní diakritiku. Přidej základní testy."},
    {"id": "text-summary", "name": "Shrnutí textu", "task": "Shrň do tří vět tento text: Agentic Economy je prostředí, ve kterém softwaroví agenti vyhledávají služby, porovnávají nabídky a nakupují výsledky práce jiných agentů. Nakupující má omezený rozpočet. Platba se uvolní po ověření dodávky. Pokud poskytovatel nedodrží podmínky, následuje reklamace a refundace."},
    {"id": "translation", "name": "Překlad", "task": "Přelož do angličtiny: Náš agent porovná nabídky poskytovatelů, objedná práci a ověří její výsledek. Za neúplnou dodávku požádá o vrácení Lux Coins."},
    {"id": "ideas", "name": "Pět nápadů", "task": "Navrhni pět konkrétních služeb pro marketplace AI agentů na hackathonu. U každé stručně vysvětli přínos pro zákazníka."},
    {"id": "http-cart-audit", "name": "Audit košíku", "task": "Prověř nákupní košík ukázkového e-shopu. Objednej kompletní HTTP audit se třemi testy co nejlevněji. Při vadné dodávce reklamuj a zkus jiného dodavatele."}
]

function item(id):
    for entry in items:
        if entry["id"] == id:
            return entry
        end
    end
    Error(InputError: "Neznámá služba: " + String(id))
end
