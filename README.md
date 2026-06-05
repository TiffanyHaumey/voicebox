# Voicebox

Modificateur de voix en temps réel, dans l'esprit de Voicemod. Capte le micro réel,
applique une chaîne d'effets DSP, et envoie le résultat vers un micro virtuel utilisable
comme entrée micro dans Google Meet, Slack et Discord.

- **Stack :** Python (numpy/scipy, sounddevice, PyQt6)
- **OS :** macOS (BlackHole), Windows (VB-CABLE), Linux (PulseAudio/PipeWire null-sink)
- **11 presets** de voix stockés en JSON

Voir la conception détaillée dans [docs/specs/2026-06-05-voice-changer-design.md](docs/specs/2026-06-05-voice-changer-design.md).

## Installation

```bash
python3 -m venv .venv
. .venv/bin/activate          # Windows : .venv\Scripts\activate
pip install -r requirements.txt
```

## Lancer l'app

```bash
. .venv/bin/activate
python main.py
```

La fenêtre liste tes périphériques d'entrée/sortie, les 11 voix, un curseur de gain, et
une case « M'entendre » (monitoring). Choisis ton micro réel en entrée, le **micro virtuel**
en sortie (voir ci-dessous), puis clique sur Start.

## Mettre en place le micro virtuel

Voicebox envoie la voix transformée vers un **périphérique audio virtuel**. Tu sélectionnes
ensuite ce périphérique comme « micro » dans Meet/Slack/Discord. La mise en place dépend
de l'OS.

### macOS — BlackHole (+ Aggregate Device)

1. Installer BlackHole 2ch : `brew install blackhole-2ch` (ou télécharger depuis
   [existential.audio/blackhole](https://existential.audio/blackhole/)).
2. **Pour t'entendre toi-même (monitoring)**, crée un *Aggregate Device* dans
   *Configuration audio et MIDI* (Audio MIDI Setup) :
   - Clique sur `+` → *Créer un appareil agrégé*.
   - Coche ton **micro réel** (entrée) **et** BlackHole **et** ton casque (sorties).
   - Dans Voicebox, choisis cet appareil agrégé comme entrée *et* sortie. Le son part
     vers BlackHole (pour Meet) et vers ton casque (pour t'entendre) en même temps.
3. Sans monitoring, tu peux simplement mettre ton micro en entrée et BlackHole en sortie.
4. Dans Meet/Slack/Discord, choisis **BlackHole 2ch** comme microphone.

### Windows — VB-CABLE

1. Installer [VB-CABLE](https://vb-audio.com/Cable/) (gratuit). Redémarrer si demandé.
2. Dans Voicebox : entrée = ton micro réel, sortie = **CABLE Input**.
3. Dans Meet/Slack/Discord, choisis **CABLE Output** comme microphone.
4. Pour t'entendre, active « M'entendre » et/ou utilise « Écouter ce périphérique » dans
   les propriétés son de CABLE Output (Windows route alors vers ton casque).

### Linux — PulseAudio / PipeWire null-sink

1. Créer un micro virtuel (null-sink) :
   ```bash
   pactl load-module module-null-sink sink_name=voicebox \
     sink_properties=device.description=Voicebox
   ```
   Cela crée une sortie « Voicebox » et un micro « Monitor of Voicebox ».
2. Dans Voicebox : entrée = ton micro réel, sortie = **Voicebox**.
3. Dans Meet/Slack/Discord, choisis **Monitor of Voicebox** comme microphone.

## Tests

```bash
. .venv/bin/activate
pytest
```

Les tests couvrent les effets DSP, la chaîne, les presets et l'état partagé. L'audio
temps réel et la sélection de périphérique se vérifient manuellement (impossible à tester
sans matériel audio).

## Notes

- Le preset **Chantante** est une version v1 simplifiée (formant + reverb) ; les harmonies
  à la tierce/octave sont prévues plus tard.
- Packaging autonome (`.app` / `.exe` via PyInstaller) prévu comme phase ultérieure.
