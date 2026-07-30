"""Catalogue -- the SQLite sample index and the search built on it.

Walk a library tree and record per-file metadata + feature vectors (indexing),
store and query them (index, query_sql), find compatible samples (search),
extract preset metadata for the index (preset_meta), track registered libraries
(registry), and resolve per-library storage paths (paths). This is the
higher-level half over the parsing engine; it pulls in analysis/ for features.
"""
