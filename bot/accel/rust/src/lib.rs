use pyo3::prelude::*;

fn estimate_tokens(s: &str) -> i32 {
    let n = s.len();
    std::cmp::max(1, ((n as i32) + 3) / 4)
}
fn est_word_tokens(w: &str) -> i32 { std::cmp::max(1, ((w.len() as i32) + 3) / 4) }

#[pyfunction]
fn chunk_text_equiv(doc_id: &str, text: &str, chunk_tokens: i32, overlap_tokens: i32)
    -> Vec<(String, i32, String, i32)> {
    let words: Vec<&str> = text.split_whitespace().collect();
    let stride = std::cmp::max(1, chunk_tokens - overlap_tokens);
    let mut out = Vec::new();
    let mut start: usize = 0;
    let mut ord: i32 = 0;

    while start < words.len() {
        let mut tok = 0;
        let mut end = start;
        while end < words.len() && tok < chunk_tokens {
            tok += est_word_tokens(words[end]);
            end += 1;
        }
        if start >= end { break; }
        let body = words[start..end].join(" ");
        if !body.is_empty() {
            out.push((format!("{}:{}", doc_id, ord), ord, body.clone(),
                std::cmp::max(1, ((body.len() as i32) + 3) / 4)));
            ord += 1;
        }
        // advance by token stride
        let mut advanced = 0;
        let mut i = start;
        while i < end && advanced < stride {
            advanced += est_word_tokens(words[i]);
            i += 1;
        }
        if i <= start { i = start + 1; }
        start = i;
    }
    out
}
#[pymodule]
fn everabot_chunk_rust(_py: Python<'_>, m: &PyModule) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(chunk_text_equiv, m)?)?;
    Ok(())
}
