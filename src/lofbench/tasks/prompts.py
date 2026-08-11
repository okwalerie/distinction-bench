"""Prompt templates for LoF evaluation tasks."""

SINGLE_SYSTEM_PROMPT = """You are an expert in evaluating Laws of Form expressions.
Your task is to analyze an expression that represents a structure of distinctions
and reduce it to its simplest form using two fundamental axioms.

## The form and axioms

### Axiom 1. The law of calling
Multiple adjacent boundaries with nothing else inside them condense into one.
Example: ()() = ()

### Axiom 2. The law of crossing
Two nested boundaries with nothing else between them annihilate to nothing.
Example: (()) = nothing

## Instructions
1. Identify the structure 
2. Look for opportunities to apply axioms (I1 or I2)
3. Apply reductions iteratively until no more reductions are possible
4. State whether the final result is marked or unmarked

If structure remains, answer marked. Answer unmarked if the expression reduces to nothing."""

SINGLE_USER_TEMPLATE = """
Here is the expression you need to evaluate:
--INPUT--
{expression}

Your response:
"""

COMPOSITE_SYSTEM_PROMPT = """You are an expert in evaluating Laws of Form expressions.
Your task is to analyze multiple expressions and determine how many reduce to marked.

## The form and axioms

We take as given the idea of distinction and the idea of indication, and that
we cannot make an indication without drawing a distinction. We take, therefore,
the form of distinction for the form.

### Definition

Distinction is perfect continence.

That is to say, a distinction is drawn by arranging a boundary with separate
sides so that a point on one side cannot reach the other side without crossing
the boundary. For example, in a plane space a circle draws a distinction.

### Axiom 1. The law of calling

The value of a call made again is the value of the call.
That is to say, for any name, to recall is to call.
Example: ()() simplifies to []

### Axiom 2. The law of crossing

The value of a crossing made again is not the value of the crossing.
That is to say, for any boundary, to recross is not to cross.
Example: [()] simplifies to nothing

## Instructions

1. For each expression, identify the structure of distinctions
2. Convert the original expression to its canonical mixed-bracket [()]{{}} representation.
  It must match the original in its foundational structure.
3. Apply axioms iteratively until no more reductions are possible
4. Determine if each reduces to marked or unmarked.

After working through ALL expressions, provide your final answers in this exact JSON format:

```json
{{
  "E1": {{"canonical": "[[]][[[()]]]([])", "result": "unmarked"}},
  "E2": {{"canonical": "()[]()[]()", "result": "marked"}},
  ...
}}
```
"""

COMPOSITE_USER_TEMPLATE = """Here are the expressions you need to evaluate:
--INPUT--
{expressions}

Your response:
"""
