#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <sstream>
#include <vector>
#include <string>
#include <algorithm>

namespace py = pybind11;

// ~4 chars per token, identical to the Python heuristic
static inline int estimate_tokens(const std::string& s) {
    const int n = static_cast<int>(s.size());
    return std::max(1, (n + 3) / 4);
}

// token length of [i, j) from precomputed prefix sums
static inline int span_tokens(const std::vector<int>& pref, int i, int j) {
    return pref[j] - pref[i];
}

static int est_word_tokens(const std::string& w) { return std::max(1, (int)((int)w.size() + 3) / 4); }

static std::vector<std::tuple<std::string,int,std::string,int>>
chunk_text_equiv(const std::string& doc_id, const std::string& text, int chunk_tokens, int overlap_tokens) {
    std::istringstream iss(text);
    std::vector<std::string> words; for (std::string w; iss >> w; ) words.push_back(std::move(w));

    const int stride = std::max(1, chunk_tokens - overlap_tokens);
    std::vector<std::tuple<std::string,int,std::string,int>> out;

    int start = 0, ord = 0;
    while (start < (int)words.size()) {
        int tok = 0;
        int end = start;
        std::ostringstream oss;
        while (end < (int)words.size() && tok < chunk_tokens) {
            if (end > start) oss << ' ';
            oss << words[end];
            tok += est_word_tokens(words[end]);
            ++end;
        }
        if (start >= end) break;
        const std::string body = oss.str();
        if (!body.empty()) {
            const std::string cid = doc_id + ":" + std::to_string(ord);
            out.emplace_back(cid, ord, body, std::max(1, (int)((int)body.size() + 3) / 4));
            ++ord;
        }
        // advance by token stride, not words
        int advanced = 0; int i = start;
        while (i < end && advanced < stride) { advanced += est_word_tokens(words[i]); ++i; }
        if (i <= start) i = start + 1;
        start = i;
    }
    return out;
}
PYBIND11_MODULE(everabot_chunk_cpp, m) {
    m.def("chunk_text_equiv", &chunk_text_equiv,
          py::arg("doc_id"), py::arg("text"),
          py::arg("chunk_tokens"), py::arg("overlap_tokens"));
}
