from app.agent.language import detect
from app.agent.sentences import CitationFilter, SentenceChunker, citations
from app.knowledge.service import split_sections


def stream(chunker, text, size=3):
    out = []
    for i in range(0, len(text), size):
        out += chunker.feed(text[i : i + size])
    return out + chunker.flush()


def test_sentences_split_across_tokens_and_keep_decimals():
    text = "The city tax is 2.50 DZD per night. Breakfast is included! Anything else?"
    assert stream(SentenceChunker(), text) == [
        "The city tax is 2.50 DZD per night.", "Breakfast is included!", "Anything else?"]


def test_first_segment_can_cut_at_clause_for_fast_speech():
    text = "Yes, we have a sea view double room available for those dates, and breakfast is included. Great."
    parts = stream(SentenceChunker(first_clause_min=40), text)
    assert parts[0] == "Yes, we have a sea view double room available for those dates,"
    assert parts[1] == "and breakfast is included."


def test_arabic_question_mark_ends_sentence():
    assert stream(SentenceChunker(), "هل تريد غرفة؟ لدينا غرف متاحة.") == ["هل تريد غرفة؟", "لدينا غرف متاحة."]


def test_citation_filter_handles_split_markers():
    f = CitationFilter()
    out = "".join(f.feed(t) for t in ["Pool opens at 8", " [F:po", "ol] and", " [C:12]closes at 7."]) + f.flush()
    assert out == "Pool opens at 8 andcloses at 7."
    assert citations("a [F:pool] b [C:12]") == {"F:pool", "C:12"}


def test_citation_filter_releases_ordinary_brackets():
    f = CitationFilter()
    assert "".join(f.feed(t) for t in ["Room [", "sea view]"]) + f.flush() == "Room [sea view]"


def test_language_detection():
    assert detect("هل لديكم موقف سيارات؟") == "ar"
    assert detect("Bonjour, avez-vous une chambre ?") == "fr"
    assert detect("Do you have parking?") == "en"


def test_split_sections_keeps_paragraphs():
    text = "A" * 500 + "\n\n" + "B" * 500 + "\n\nshort"
    chunks = split_sections(text, limit=900)
    assert chunks[0] == "A" * 500 and chunks[1].startswith("B" * 500)


def test_noise_transcripts_are_rejected():
    from app.agent.language import plausible_transcript

    langs = ["en", "ar", "fr"]
    assert not plausible_transcript("เอ่ออัน", langs)
    assert not plausible_transcript("好，Q。", langs)
    assert not plausible_transcript("Ah", langs)
    assert plausible_transcript("Ah, ok.", langs)
    assert plausible_transcript("Hi, can I know if you have an available room for tonight?", langs)
    assert plausible_transcript("هل يوجد موقف سيارات؟", langs)
    assert not plausible_transcript("هل يوجد موقف سيارات؟", ["en", "fr"])


def test_markdown_is_stripped_across_tokens():
    from app.agent.sentences import MarkdownFilter

    f = MarkdownFilter()
    tokens = ["I found:\n\n*", "*Sea View", " Room**\n- Total: 37", ",000 DZD\n## Next\n", "check_in stays"]
    assert "".join(f.feed(t) for t in tokens) == "I found:\n\nSea View Room\nTotal: 37,000 DZD\nNext\ncheck_in stays"


def test_arabic_normalization_matches_prefixed_and_variant_forms():
    from app.knowledge.bm25 import BM25Index, tokenize

    assert tokenize("والمطار") == tokenize("المطار") == tokenize("مطار")
    assert tokenize("مدينة") == tokenize("مدينه")  # taa marbuta
    assert tokenize("أين متى إلى") == []  # normalized stopwords
    index = BM25Index([{"id": "a", "content": "يبعد المطار عن المدينة حوالي 4 كم"},
                       {"id": "b", "content": "الشاطئ رملي والمياه صافية"}])
    assert index.search("كم يبعد مطار المدينه؟")[0][1]["id"] == "a"


def test_bm25_prefers_passages_covering_more_query_terms():
    from app.knowledge.bm25 import BM25Index

    index = BM25Index([
        {"id": "list", "content": "Sahl Hasheesh hotels: Sahl Hasheesh Resort, Sahl Hasheesh Palace, Sahl Hasheesh Inn"},
        {"id": "where", "content": "Sahl Hasheesh is a bay located 18 km south of the airport near Hurghada"},
    ] + [{"id": f"x{i}", "content": f"unrelated passage number {i} about diving"} for i in range(20)])
    assert index.search("Where is Sahl Hasheesh located near the airport?")[0][1]["id"] == "where"
