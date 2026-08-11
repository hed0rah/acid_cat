"""Infra -- the cross-cutting plumbing the rest of core sits on.

Format detection by magic (sniff), the shared value->label vocabulary (vocab),
optional parse sandboxing (sandbox), output rendering to table/JSON/CSV
(formats), bounded random-access file bytes (mapped), and the low-level
typed-field read/decode utilities (bytefields, fieldcodec).
"""
