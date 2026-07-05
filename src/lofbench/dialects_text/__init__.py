"""Phase A text dialects for DB-2 (task_01KWQKYTN19RZN2BFKAAGQCFKA).

Each module in this package is a standalone `render_X` / `parse_X` pair, not
yet wrapped as a DB-4 `Archetype`. See `.lattice/plans/task_01KWQKYTN19RZN2BFKAAGQCFKA.md`
for the phased plan: this package is Phase A only. Phase B (registry
wiring, injectors, provenance) is blocked on DB-4 landing its migration
plan's M1 and M2 milestones, and is not implemented here.

Every module depends only on `lofbench.core` (`string_to_form`, needed to
turn a canonical form string into the nested-list representation these
renderers walk, and to check round-trip correctness in tests).
"""
