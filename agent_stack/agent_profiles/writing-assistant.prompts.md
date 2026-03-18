# Writing Assistant Prompt Templates

## Names
Suggest 10 unique character names suitable for the following context. Return as a markdown bullet list with a short description for each.

Context:
- Genre: {genre}
- Setting: {setting}
- Era: {era}
- Notes: {notes}

## Technology
Describe 5-10 pieces of technology, inventions, or scientific concepts relevant to the story. Return as a markdown list with a name and 1-2 sentence description for each.

Context:
- Genre: {genre}
- Setting: {setting}
- Era: {era}
- Story needs: {needs}

## Dates & History
Generate a timeline of 5-10 key historical events, including dates and a brief description. Return as a markdown table or bullet list.

Context:
- World/setting: {setting}
- Major themes: {themes}
- Existing history: {history}

## Personalities
List 5-10 character archetypes or personalities, each with a name, 2-3 traits, and a short backstory. Return as a markdown list.

Context:
- Genre: {genre}
- Story focus: {focus}
- Desired diversity: {diversity}

---

# Usage
- Fill in curly-brace fields with relevant context from the book project.
- Always request markdown output for easy export.
- Encourage the model to ask for clarification if context is ambiguous.
