"""Voice mode: the audio timeline, perception, the conversation reducer, runtime.

See docs/voice-mode/architecture-2026-08-12.md. The one rule that keeps this
package coherent: `machine.py` holds all the policy and is pure; everything else
either produces an event for it or performs an effect from it.
"""
