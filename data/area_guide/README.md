# Hurghada area guide (retrievable knowledge and RAG test corpus)

Text from Wikipedia articles, fetched with `scripts/fetch_area_guide.py`. Each JSON file keeps its
`title`, `url`, `license` and `retrieved` date. Wikipedia text is licensed under
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) by its contributors; the source
URL is kept on every passage in the database (`knowledge_chunks.source`) and shown to the model.

This is general area information (distances, attractions, visa and currency basics). It is not
presented as hotel policy: the agent's prompt labels these passages as area information.

`../rag_eval.json` holds 28 questions (18 English, 10 Arabic) whose exact answer phrase exists in
this corpus; `scripts/rag_eval.py` measures retrieval recall and latency on it.
