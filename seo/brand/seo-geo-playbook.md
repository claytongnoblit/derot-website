# SEO + GEO writing playbook (loaded into writer and reviewer prompts)

GEO = generative engine optimization: being the page that ChatGPT search, Google AI Overviews / AI Mode, Perplexity, Claude, and Copilot retrieve, trust, and quote. Classic SEO and GEO mostly reward the same things: clear answers, real expertise, structure, and evidence.

## Search intent first
- Decide what the searcher actually wants (a quick how-to, an explanation, a comparison, a list) and give it to them early. The format must match what ranks for the query, then beat it on depth, clarity, and originality.
- The primary keyword goes in the title, the first 100 words, at least one H2, and the meta description, phrased naturally. Use secondary keywords and natural variants in H2s and body. Never keyword-stuff.

## Answer-first structure (the biggest GEO lever)
- Open with a 2-3 sentence direct answer to the core query (40-60 words) that would make sense quoted on its own, with no "In this article" preamble.
- Provide 3-5 "Key takeaways" bullets (the template renders them at the top).
- Each H2 should be a question or a clear claim a person would search for. Start the first paragraph under each H2 with a self-contained 1-2 sentence answer, then expand.
- Write quotable definitions: "Box breathing is a ... technique in which you ...". Name the thing, define it, give the key number.
- Use specific numbers with attribution ("a 2023 Stanford study of 108 people found ..."), each linked to its source. Statistics with sources are among the most-cited sentences in AI answers.
- Use short paragraphs (1-4 sentences), numbered steps for procedures, and a markdown table when comparing 3+ items across attributes.
- Keep each section self-contained enough to be extracted as a passage without the rest of the page.

## E-E-A-T and trust
- Cite primary research and authoritative bodies (journals, NIH/PubMed, universities, major clinics). Link the exact claim, not a homepage.
- Show experience: practical detail on how a technique feels, common mistakes, how to adapt it in a real scroll moment, what to do if it feels uncomfortable.
- Be honest about limits of the evidence. Overclaiming kills trust with readers and with AI systems that cross-check.
- Include a brief safety note where a technique involves breath holds, fast breathing, or might cause lightheadedness.

## Internal linking (topical authority)
- Link to 2-5 relevant existing DeRot pages using descriptive anchor text (not "click here"). Only use URLs from the provided site inventory.
- Link to the relevant free breathing tool when the article teaches a technique.

## FAQ section
- 3-6 genuine follow-up questions people ask (People Also Ask style), not repeats of H2s. Answers 40-90 words, direct, standalone. They are rendered visibly and as FAQPage schema.

## Formatting rules for the body markdown
- Do NOT include an H1 (the template renders the title). Use ## for sections and ### for subsections.
- Do NOT include the key takeaways, FAQ, or sources list in the body; those are separate fields.
- Inline citations are markdown links on the claim text: "[slow breathing at about six breaths per minute](https://...)". Only use URLs from the research brief's source list.
- No images, no HTML, no footnote syntax, no emoji.
- Use plain straight quotes or typographic quotes consistently. No em-dashes or en-dashes.

## Comparison and alternatives articles
- Be fair and accurate about other apps. Describe competitors only with facts supported by their own websites or App Store listings (cite them), never speculate about pricing or features, and never disparage. Readers trust comparisons that say when another tool is the better fit.
- Explain honestly who DeRot is and is not for: it calms you down mid-scroll rather than blocking you at the door; people who want a hard lockout may prefer a blocker.
- Never claim DeRot ratings, user counts, or outcomes. Use the launch status given in the prompt (pre-launch or live) when describing availability.
