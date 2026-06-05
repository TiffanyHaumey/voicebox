# Voicebox

Modificateur de voix en temps réel, dans l'esprit de Voicemod. Capte le micro réel,
applique une chaîne d'effets DSP, et envoie le résultat vers un micro virtuel utilisable
comme entrée micro dans Google Meet, Slack et Discord.

- **Stack :** Python (numpy/scipy, sounddevice, PyQt)
- **OS :** macOS (BlackHole), Windows (VB-CABLE), Linux (PulseAudio/PipeWire null-sink)
- **11 presets** de voix stockés en JSON

Voir la conception détaillée dans [docs/specs/2026-06-05-voice-changer-design.md](docs/specs/2026-06-05-voice-changer-design.md).

## Statut

En cours de conception → implémentation. Pas encore exécutable.
