# Pokémon Smeraldo in italiano

Emerald 3DS funziona con la ROM italiana pulita di
**Pokémon Versione Smeraldo (Italia, BPEI)**, con i suoi testi e le sue grafiche. La
traduzione non viene distribuita: arriva dalla tua ROM, sul tuo computer o nel browser.

La variante è stata verificata compilando: il port si collega senza riferimenti non
risolti e il pacchetto dati si costruisce a partire da BPEI. Non è ancora stata provata su
console né su emulatore, e l'avventura completa non è stata giocata fino in fondo.

## Installare

Come la versione inglese: il builder web e quello per Windows accettano tutte le ROM
supportate e scelgono la lingua da soli. Ogni release contiene le varianti, ognuna con la
sua ricetta. Per l'aggiornamento rapido (il solo 3DSX) la pagina web controlla il tuo
`emerald3ds.pak` e ti propone `Emerald3DS-it.3dsx`, da salvare sulla scheda SD con il nome
`Emerald3DS.3dsx`.

| Lingua | Codice | SHA-1 della ROM pulita |
|---|---|---|
| Italiano | BPEI | `1692db322400c3141c5de2db38469913ceb1f4d4` |
| Francese | BPEF | `ca666651374d89ca439007bed54d839eb7bd14d0` |
| Inglese | BPEE | `f3ae088181bf583e55daf962a92bb46f4f1d07b7` |
| Spagnolo | BPES | `fe1558a3dcb0360ab558969e09b690888b846dd9` |

## Compilare la versione italiana dal codice

Con i prerequisiti della [guida di sviluppo](DEVELOPMENT.md):

```sh
python tools/bootstrap.py --make --italian-rom "/percorso/Pokemon - Versione Smeraldo (Italy).gba" -j8
```

Il bootstrap applica le patch, compila gli strumenti e gli include generati, esegue
`tools/localize_italian.py` con la tua ROM e compila il 3DSX in
`build/upstream/3ds_port/emerald3ds.3dsx`. Cambiare lingua (rilanciando senza
`--italian-rom`) azzera l'albero e cancella gli oggetti compilati e le risorse localizzate,
per non riusare i dati di un'altra lingua.

Il motore controlla la lingua con cui è stato compilato: un eseguibile italiano accetta
solo un pacchetto dati costruito da BPEI, e viceversa.

## Come si ottiene la versione italiana

`tools/localize_italian.py` verifica lo SHA-1 della ROM e le impronte SHA-256 dei file
sorgente prima di scrivere nell'albero generato. I manifesti di `tools/locales/`
contengono posizioni nel codice, scostamenti e dimensioni nella ROM, nomi di simboli e
indirizzi. Non contengono nessun testo, grafica, suono o sezione della ROM.

Il processo sostituisce testi e campi del codice con quelli della tua ROM, estrae 104
risorse grafiche e ricostruisce le 55 pagine dei crediti e i piani del Monte Allenamento.
Passano per lì i nomi, i dialoghi, i menu, il vocabolario, le canzoni del bardo, le frasi
degli allenatori, le mappe, le domande e i testi in braille delle rovine.

Cose specifiche dell'italiano:

- il Pokédex mostra metri e chili con la **virgola** decimale e l'allineamento adatto,
  come il francese;
- la tabella dei tipi è più larga, perché « una mossa COLEOTTERO » non entra in quella
  inglese;
- le bacche restano una parola sola (« BACCARANCIA » è « BACCA » + « RANCIA », senza
  spazio, come nella ROM italiana) e i nomi si compongono di conseguenza;
- la schermata « LA FINE » usa la tilemap pre-renderizzata della ROM italiana
  (`graphics/credits/italian_the_end.bin.lz`);
- il messaggio di squadra del condividi esperienza, che la ROM italiana non ha, è scritto
  nella patch (« Il resto della squadra riceve punti ESP.! »), come fa il francese;
- i quattro accenti che servono all'italiano e che mancavano alla tavola dei glifi
  (`ì`, `ò`, `Ì`, `Ò`) sono stati aggiunti.

I menu del port sono tradotti separatamente: OPZIONI, MIGLIORIE e TRUCCHI, le loro voci e
i loro valori, le tasche della borsa (OGGETTI, POKé BALL, MT E MN, BACCHE, STRUMENTI), le
statistiche (PS, ATTACCO, DIFESA, ATT. SP., DIF. SP., VELOCITA) e le etichette brevi
d'azione. Le etichette sono volutamente **senza accenti**, perché la tabella del testo
touch del port mappa solo l'ASCII.

Se un cambiamento del port modifica un file sorgente del manifesto, la localizzazione si
ferma (« Source differs from the pinned patched tree ») finché le sue posizioni e le sue
impronte non vengono aggiornate.

## Foto per la revisione

La variante deve ancora essere provata su console. Fotografare lo schermo inferiore
intero, senza ritagliare bordi o schede:

- OPZIONI, scheda delle impostazioni: le dodici celle, i valori e le schede.
- MIGLIORIE: le opzioni, comprese le voci aggiunte dal port.
- TRUCCHI: le due pagine, con il numero di pagina e i valori visibili.
- Da POKéMON, il riepilogo di un Pokémon: nome, tipi, statistiche, natura, abilità e
  mosse con i PP; privilegiare nomi lunghi.
- In lotta, la scelta di una mossa: le quattro caselle con nomi, tipi e PP.
- Il Pokédex: altezza e peso di un Pokémon, per controllare metri, chili e virgola.
- I crediti e la schermata finale « LA FINE ».

Annotare il commit compilato e il modello di console con le foto. I controlli di larghezza
fatti al computer non sostituiscono queste foto né le prove su console.

Il port usa percorsi SD fissi (`/3ds/emerald3ds/`) per i dati, le impostazioni e il
salvataggio, anche se il 3DSX viene avviato da un'altra cartella. Per provare una
compilazione senza toccare un'installazione esistente, usare una scheda SD di prova
separata.

## Test senza ROM

```sh
python -m unittest discover -s builder/tests -v
```

## Autore

Traduzione italiana e strumenti di localizzazione di andyst-dev.
