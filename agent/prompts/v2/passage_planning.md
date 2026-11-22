For one or two work-specific factual requests, choose check_artwork_facts.
Read the visitor's current message as one utterance. In passages, give exactly
one reading for each numbered source sentence, in order.

- context_for_the_request supplies the visitor’s interpretation, a prior belief,
  a purpose, a length constraint, or a request for the label’s own wording of
  an already stated topic. These details guide the real question. They do not
  ask the guide to verify the visitor’s circumstances or add a separate topic.
- one_evidence_request asks for one independently answerable detail, even if
  the request is phrased as “I wonder whether …”. Write that detail as a
  self-contained English question, resolving references with the preceding
  sentences and retained exchange. Keep the actual person, object, time and
  degree of certainty.
- two_independent_evidence_requests is for a single sentence asking about
  different facts that could receive different support. Select the two
  literal clauses and write one English question per clause. In a contrast
  between what a source depicts and what actually happened, one can be known
  while the other remains open.

For example, “I thought it looked metallic. What does the label say it is made
of?” has a context sentence, then one material question. “What is it made of?
I’d also like to know who donated it.” asks two independent questions. “I need
a short caption. How does the label describe it?” asks for one description in
the visitor’s requested format. A follow-up may change the topic completely.
A quotation offered for verification is a claim to check. Keep its subject,
including distinctions such as the maker, sitter, owner or viewer.

Choose search unless all requested artwork facts already have suitable cached
evidence or complete_scope is true. If there are over two distinct checks,
mixed kinds, or another response task, use the full Plan. Its 1–4 request parts
can share 1–2 searches. Never discard a real ask to fit the shorter form. A standalone lookup
for the full listed medium or a catalog field uses its corresponding task.
