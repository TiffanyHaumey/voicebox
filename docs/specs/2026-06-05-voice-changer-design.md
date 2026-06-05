# Voice Changer — Design Document

**Date:** 2026-06-05
**Status:** Validated design, pending spec review

## 1. Objectif

Construire un modificateur de voix en temps réel, dans l'esprit de Voicemod, permettant
d'appliquer différentes "voix" (narrateur, aiguë, grave, robot, etc.) à sa voix pendant
des réunions et des appels. L'usage cible principal est la visioconférence (Google Meet),
mais l'app doit aussi fonctionner avec Slack et Discord.

Cas d'usage cible : réunions Meet (pas de chant live), donc une latence de l'ordre de
30–80 ms est acceptable.

## 2. Principe de fonctionnement

Une page web ne peut pas s'exposer comme périphérique micro pour d'autres applications.
La seule architecture qui rend la voix transformée disponible dans Meet/Slack/Discord est
une application desktop combinée à un **périphérique micro virtuel** au niveau système :

```
Micro réel → [App de traitement] → Périphérique audio VIRTUEL → choisi comme "micro" dans Meet/Slack/Discord
```

1. Capter le micro réel.
2. Transformer la voix via une chaîne d'effets DSP.
3. Écrire le résultat vers un périphérique de sortie virtuel.
4. L'utilisateur sélectionne ce périphérique virtuel comme "micro" dans chaque application.

Comme le micro virtuel est un périphérique système, il fonctionne partout, y compris dans
Meet ouvert dans un navigateur.

## 3. Décisions clés (validées)

- **Type de voix :** effets DSP temps réel (pas de conversion IA pour ce premier jet).
- **Stack :** Python (numpy/scipy pour le DSP, sounddevice pour l'audio, PyQt pour la GUI).
- **Architecture :** moteur audio temps réel isolé de la GUI (voir §4).
- **Micro virtuel configurable** selon l'OS :
  - macOS : BlackHole (gratuit, open source) — point de départ du développement.
  - Windows : VB-Audio VB-CABLE.
  - Linux : null-sink PulseAudio/PipeWire (natif, aucune install tierce).
- **Monitoring** ("M'entendre") : toggle dans la GUI, **OFF par défaut**.
- **11 presets** livrés (voir §6).
- **Spatialisation** = reverb / sensation d'espace uniquement (pas de panoramique stéréo,
  perdu en mono dans la plupart des visios).
- **Multi-OS** : Mac + Windows + Linux pris en charge par conception.
- **Packaging** : conçu "packaging-friendly" dès maintenant (chemins relatifs, ressources
  embarquables) ; packaging PyInstaller en phase finale séparée, après validation des presets.

## 4. Architecture

Approche retenue : **moteur audio et GUI séparés par threads**.

```
┌─────────────┐     ┌──────────────────────────────┐     ┌────────────┐
│ Micro réel  │────▶│  App Python                  │────▶│ Micro      │
│             │     │                              │     │ virtuel    │
└─────────────┘     │  thread AUDIO (temps réel)   │     └─────┬──────┘
                    │   capture → chaîne effets →  │           │
                    │   écrit vers sortie virtuelle│           ▼
                    │         ▲                    │     ┌────────────┐
                    │         │ params thread-safe │     │ Meet/Slack │
                    │  thread GUI (PyQt)           │     │ Discord    │
                    │   boutons voix + sliders     │     └────────────┘
                    └──────────────────────────────┘
                              │ (si monitoring ON)
                              ▼
                        Casque utilisateur
```

- Le traitement audio tourne dans le **callback temps réel** de sounddevice (thread audio
  prioritaire), totalement isolé de la GUI.
- La GUI tourne dans le thread principal et ne fait qu'**écrire des changements de
  paramètres** dans un objet partagé thread-safe. Elle ne touche jamais l'audio
  directement — c'est ce qui évite les coupures quand l'utilisateur clique.
- Règle d'or temps réel : pas d'allocation lourde ni d'I/O dans le callback audio.

### Paramètres audio fixes

- **Fréquence d'échantillonnage : 48000 Hz** partout. BlackHole et VB-CABLE sont à 48 kHz
  par défaut ; on impose 48 kHz au stream. Si le micro réel n'expose pas 48 kHz nativement,
  le système/host API rééchantillonne. Le périphérique virtuel doit être configuré à 48 kHz
  (documenté dans le guide d'install).
- **Taille de bloc : 512 samples** (~10,7 ms à 48 kHz). Choix : compromis entre latence et
  marge de calcul pour les effets coûteux ; laisse de la place sous le budget 30–80 ms.
- **Canaux : mono.** L'entrée est downmixée en mono avant la chaîne DSP (certains micros
  renvoient du stéréo). La sortie est dupliquée sur les canaux requis par le périphérique
  virtuel.

### Couplage entrée/sortie (point critique)

Un `sd.Stream` duplex exige que l'entrée et la sortie partagent le même host API/périphérique.
Or micro réel et périphérique virtuel sont deux appareils distincts. Solutions par OS :

- **macOS :** créer un **Aggregate Device** combinant le micro réel + BlackHole, et ouvrir
  le stream duplex sur cet aggregate. **Étape d'install obligatoire**, documentée dans le
  guide. L'app détecte l'aggregate ou guide l'utilisateur pour le créer.
- **Windows :** VB-CABLE expose une entrée et une sortie ; on ouvre **deux streams séparés**
  (input = micro réel, output = CABLE Input) reliés par un **ring buffer** côté app.
- **Linux :** PipeWire/PulseAudio permet de router librement ; stream input micro réel +
  output vers le null-sink, reliés par ring buffer si nécessaire.

Décision de conception : `audio_io.py` supporte **les deux modèles** — duplex (aggregate) et
double-stream relié par ring buffer — et choisit selon l'OS/périphériques disponibles. Le
modèle double-stream + ring buffer est le fallback universel.

### Flux audio détaillé

1. Ouverture du/des stream(s) à 48 kHz, blocs de 512 samples, mono interne.
2. À chaque bloc, downmix mono → le callback applique la chaîne d'effets du preset actif.
3. Le bloc transformé est écrit vers le périphérique virtuel.
4. Si le monitoring est ON, **le même bloc transformé déjà calculé** est aussi écrit vers le
   casque (voir §4bis). Jamais de recalcul.

### 4bis. Monitoring

Toggle "M'entendre", OFF par défaut. Pour éviter la dérive de deux streams indépendants,
le monitoring **ne crée pas un second stream désynchronisé** : la sortie virtuelle et la
sortie casque sont alimentées depuis le **même appel de callback** (mêmes échantillons, même
horloge). Concrètement, le périphérique de sortie utilisé par le callback combine les canaux
virtuel + casque (via un Aggregate Device de sortie sur macOS, ou écriture multi-cible côté
ring buffer sur Windows/Linux). Quand le monitoring est OFF, seul le périphérique virtuel
reçoit l'audio. À désactiver en réunion (sinon léger retour différé gênant).

## 5. Organisation du code

```
voicebox/
├── engine/
│   ├── audio_io.py       # ouverture stream micro→sortie(s), gestion périphériques
│   ├── effects.py        # briques DSP : pitch, formant, eq, reverb, ring_mod...
│   ├── chain.py          # applique une liste d'effets à un bloc audio
│   └── params.py         # état partagé thread-safe (preset actif, monitoring on/off)
├── presets/
│   ├── narrateur.json
│   ├── robot.json
│   └── ... (11 fichiers)
├── gui/
│   └── app.py            # fenêtre PyQt : boutons presets, sliders, sélecteur sortie, toggle monitoring
├── main.py               # assemble engine + gui
└── tests/
    └── test_effects.py   # tests des effets sur des buffers connus
```

### Frontières / responsabilités

- `effects.py` : ne connaît ni l'audio temps réel ni la GUI. Pur `bloc entrant → bloc
  sortant`. Testable isolément.
- `chain.py` : lit un preset (liste d'effets) et les enchaîne. Testable isolément.
- `audio_io.py` : gère uniquement les flux et la sélection de périphériques. Le callback
  temps réel lit `params` et appelle `chain`.
- `params.py` : état partagé entre GUI et thread audio, **conçu sans lock** dans le chemin
  audio (un lock dans le callback risque l'inversion de priorité et des glitches). Mécanisme :
  changement de preset via une `queue.SimpleQueue` (la GUI pousse, le callback dépile en
  début de bloc) ; valeurs continues des sliders (pitch, dosage reverb) lues comme simples
  `float` Python (lecture/écriture atomique sous le GIL, tolérance à un bloc de retard
  acceptable).
- `gui/app.py` : écrit seulement dans `params`, jamais dans l'audio.

Tous les chemins de ressources (presets) sont relatifs au programme, pas en dur, pour
faciliter l'embarquement par PyInstaller ultérieurement.

## 6. Effets DSP & presets

Une "voix" = un **preset** = liste ordonnée d'effets avec paramètres, stockée en JSON.
Ajouter une voix = écrire un fichier JSON, pas du code.

### Briques d'effets

| Effet | Rôle |
|---|---|
| pitch_shift | monte/descend la hauteur |
| formant_shift | change le timbre sans changer la hauteur |
| eq / filtre | passe-bas/haut/bande (radio, téléphone, voix chaude) |
| distortion / bitcrush | sature, dégrade (robot, alien) |
| ring_mod | modulation en anneau (métallique / Dalek) |
| reverb | ajoute de l'espace (narrateur, grotte) |
| delay / echo | répétitions |
| chorus | doublure/épaississement (voix harmonisée) |
| gain | volume de sortie |

### Exemple de preset JSON

```json
{
  "name": "Narrateur",
  "chain": [
    { "effect": "pitch_shift", "semitones": -3 },
    { "effect": "eq", "type": "lowshelf", "freq": 200, "gain": 4 },
    { "effect": "reverb", "room": 0.6, "wet": 0.25 }
  ]
}
```

### Les 11 presets livrés

| # | Preset | Effets (chaîne) |
|---|---|---|
| 1 | Robot | ring_mod + bitcrush + eq |
| 2 | Grave / Deep | pitch_shift −5/−7 + lowshelf |
| 3 | Narrateur | pitch −3 + eq chaude + reverb légère |
| 4 | Aiguë / Chipmunk | pitch_shift +6/+8 |
| 5 | Démon / Earthquake | pitch −8 + distortion + reverb |
| 6 | Alien | ring_mod + pitch + chorus/delay |
| 7 | Bébé | pitch + formant up |
| 8 | Grotte / Cave | reverb forte + delay |
| 9 | Radio / Téléphone | bandpass + bitcrush léger |
| 10 | Femme ↔ Homme | formant_shift (± sans toucher le pitch) |
| 11 | Chantante (mélodieuse) | chorus + harmonies (tierce/octave) + reverb |

### Budget de latence et choix du pitch shifter

Le budget total est 30–80 ms. Décomposition par bloc de 512 samples à 48 kHz :

- Buffer entrée + sortie OS : ~2 × 10,7 ms ≈ 21 ms.
- Traitement par bloc : doit rester bien < 10,7 ms (durée d'un bloc) pour éviter l'underrun.

Décision sur le pitch shift : **on n'utilise PAS pyrubberband en temps réel** (son look-ahead
de 512–2048 samples ajoute 10–42 ms et menace les chaînes lourdes comme Démon/Alien). On
implémente un pitch shifter **léger** : resampling + overlap-add (PSOLA) ou phase vocoder
court à fenêtre réduite, calibré pour < 5 ms d'algorithmique. La qualité est moindre que
pyrubberband mais largement suffisante pour des voix d'effet (robot, aigu, grave), et c'est
ce qui tient le budget temps réel.

Reverb : implémentation légère (Schroeder/feedback delay network court), wet/dry mixé.

Si une chaîne dépasse le budget et décroche, le callback **bypasse** (laisse passer l'audio
non traité plutôt que du silence) et signale dans la GUI.

### Cas particulier — preset 11 "Chantante"

Les harmonies (doublure à la tierce/octave) nécessitent une **2e voix pitch-shiftée
indépendante** mixée à l'original → coût ~2× un pitch_shift. Preset marqué comme
**potentiellement coûteux** : à valider en dernier, et à simplifier (chorus + reverb seuls,
sans harmonie) si le budget temps réel ne tient pas.

## 7. GUI (PyQt, minimaliste)

- Grille de **11 boutons** (un par voix), preset actif surligné.
- Quelques **sliders live** pour les paramètres clés du preset actif (ex. pitch, dosage reverb).
- **Menu déroulant "sortie virtuelle"** (BlackHole / VB-CABLE / null-sink selon l'OS).
- **Case "M'entendre"** (monitoring, OFF par défaut) + sélecteur du casque de monitoring.

## 8. Tests

- **Effets (auto) :** tests sur buffers connus (ex. sinus 220 Hz pitché de +12 demi-tons
  doit ressortir ~440 Hz). Valide le DSP sans écoute.
- **Chaîne temps réel (manuel) :** monitoring ON, l'utilisateur parle et s'entend
  transformé. L'oreille valide les presets.
- **Intégration (manuel) :** appel test dans Meet/Discord en sélectionnant le micro virtuel.

## 9. Gestion des erreurs (aux frontières)

- Périphérique introuvable / micro virtuel non installé → message clair dans la GUI avec
  lien d'installation (BlackHole / VB-CABLE / instructions Linux).
- Underrun audio (effet trop lourd qui fait décrocher) → le callback passe en **bypass**
  (audio non traité plutôt que silence, moins gênant en réunion) + log + signalement GUI.
- Preset JSON malformé → validé au **chargement** (hors callback) via un schéma simple ;
  un preset invalide est ignoré avec message GUI, jamais chargé dans le chemin audio.

## 10. Hors scope (pour ce premier jet)

- Conversion de voix par IA (RVC / voice cloning).
- Panoramique stéréo / spatialisation directionnelle.
- Auto-Tune / pitch correction temps réel.
- Raccourcis clavier globaux (changement de voix sans focus).
- Driver Core Audio maison (on utilise BlackHole).
- Packaging/distribution signés (phase ultérieure ; PyInstaller prévu mais non inclus ici).

## 11. Phases ultérieures envisagées

- Packaging PyInstaller (.app / .exe autonomes) une fois les presets validés.
- Guide d'installation pas-à-pas par OS pour le micro virtuel (distribution aux amis).
- Éventuelle signature/notarisation pour supprimer les avertissements de sécurité OS.
