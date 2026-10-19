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

## Comparison and alternatives articles (roundups, "X vs Y", "X alternative")
These posts are written to be the page AI answers quote for "best app for..." and "X vs Y" questions. Research behind these rules: Wix AI Search Lab (1M+ citations, 2026) found roundups are the most-cited format on commercial prompts and that 81% of cited roundups were third-party, not brand-run. Google began demoting brand blogs that rank their own product #1 in "best X" lists in early 2026. AI systems favor neutral, verifiable, well-structured comparisons.

### Neutrality and DeRot's place
- Never rank DeRot. In a roundup, the ranked or numbered list contains only other apps. DeRot gets one short, clearly labeled section near the end ("Where DeRot fits (our app)") that says what it does differently and who it is not for.
- Disclose in the first 100 words, in plain words: "DeRot makes [this site / an app in this category]. We do not rank our own app, and no app paid to be included."
- In a head-to-head ("one sec vs Opal"), the comparison is between the two named apps. DeRot may appear only after the verdict, as a third option for readers who want neither approach.
- Be fair and accurate about other apps. Never disparage. Say plainly when another tool is the better fit, and for whom. People who want a hard lockout may prefer a blocker; say so.
- Never claim DeRot ratings, user counts, reviews, or outcomes. Use the launch status given in the prompt (pre-launch or live) when describing availability.
- Keep DeRot's positioning rules: it meets you mid-scroll; never "breathe before you open"; never "locks."
- The body may name DeRot at most 4 times (an automated cap): the disclosure, the DeRot section, and at most one more. Keep the summary table DeRot-free in roundups.
- Still cite at least 2 research or authoritative sources (an automated gate). Good fits: peer-reviewed studies of friction or delay interventions, slow-breathing and exhale research, and research on how scrolling affects mood.

### Facts about other apps
- Describe competitors only with facts supported by their own website or App Store listing, cited inline. Independent research (for example a peer-reviewed study of friction apps) can be cited for how an approach works.
- Never speculate about pricing or features. Prices carry currency and billing period and a check date ("$X/year as listed on the App Store, checked October 2026"). If a fact is not stated by the app, write "not stated" or leave it out.
- Do not use star ratings, scores out of 10, or review counts you did not measure. No invented testing ("we tested for 30 days") unless the brief documents it.

### Structure (in this order)
1. Answer-first opening (40-60 words) that names the short answer: the top picks and who each is for, or the one-line verdict for a head-to-head.
2. The disclosure sentence (can be part of the opening paragraph).
3. A summary markdown table right after the opening: one row per app, with the same columns every time, for example App | Best for | How it works | Free option | Price (as listed) | Platforms. Every cell is a short, self-contained fact.
4. "How we compared" H2: 3-5 named criteria (for example how it intervenes, how strict it is, what it costs, privacy, platforms) and the date the facts were checked.
5. One H3 per app using the same fields in the same order: Best for, How it works, What stands out, Drawbacks, Price. Parallel structure lets AI systems compare field by field.
6. A "Which should you pick?" H2 written as if-then lines: "If you want X, pick Y." This is the section most likely to be quoted.
7. "Where DeRot fits (our app)" (roundups) or the third-option note (head-to-heads), short.
8. FAQ as usual.

### Titles and freshness
- Titles state the comparison plainly ("one sec vs Opal: friction or blocking?", "Best breathing apps for iPhone, compared"). No year in the title, no "ultimate", no "top 10" padding.
- Freshness comes from real rechecks: a refresh updates prices and features from the apps' own pages, not just the date.
