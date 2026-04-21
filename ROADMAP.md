# Roadmap

This document contains the intended development plan.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [1.3.0] 

### Change toolbar button behavior. Make Pause, Play, Stop work as intuitively expected.
- Pause to pause playback and put us in a paused state.
- When in a paused state, either Pause or Play will resume.
- Stop to stop playback and put us in a stopped state.
- While in a stopped state, Play will initiate playback from the beginning of the current clipboarded text.

### Change server audio playback to use sd.OutputStream in place of sd.play
- Resolves resource leak with multiple streams as different chunks are played.

### Fixed duplicated Step #2 in INSTALL.md

---

## [1.4.0] 

### Implement frontpocket_installer.sh to make installation easier. 
- Leave the manual installation steps in the document.
- This will be tested in Debian. Other distros please report back and open a PR with any fixes.

---

## [1.5.0] 

### Better multilingual support using Pocket-TTS 2.0
- Ability to specify language for sentence chunking and voice.
- Set default voice/speed per language.
- Detect language from text and automatically pick the default voice to use for that language.
