"""Data collectors: each one reads one source (window, git, calendar) and
returns plain data. None of them write to storage.py themselves, and none
of them may raise for an ordinary "unavailable" condition — that's a
result (None / empty list), not an exception.
"""
