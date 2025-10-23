Build with:

```bash
python -m pip install -v .
```


###### `bot/accel/cpp/src/chunk_ext.cpp`
```cpp
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <string>
#include <vector>
#include <sstream>
#include <algorithm>

namespace py = pybind11;

// Approximate token estimator identical to Python's: ceil(len(text)/4)
static inline int estimate_tokens(const std::string& s) {
  const size_t n = s.size();
  return std::max(1, int((n + 3) / 4));
}

static std::vector<std::tuple<std::string,int,std::string,int>>
chunk_text_equiv(const std::string& doc_id,
                 const std::string& text,
                 int chunk_tokens, int overlap_tokens) {
  std::istringstream iss(text);
  std::vector<std::string> words;
  std::string w;
  while (iss >> w) words.emplace_back(w);

  const int step = std::max(1, chunk_tokens - overlap_tokens);
  std::vector<std::tuple<std::string,int,std::string,int>> chunks;
  int i = 0, ord = 0;

  while (i < (int)words.size()) {
    const int end = std::min((int)words.size(), i + chunk_tokens);
    if (i >= end) break;
    std::ostringstream oss;
    for (int j = i; j < end; ++j) {
      if (j > i) oss << " ";
      oss << words[j];
    }
    const std::string body = oss.str();
    if (!body.empty()) {
      const std::string cid = doc_id + ":" + std::to_string(ord);
      chunks.emplace_back(cid, ord, body, estimate_tokens(body));
      ++ord;
    }
    i += step;
  }
  return chunks;
}

PYBIND11_MODULE(everabot_chunk_cpp, m) {
  m.doc() = "Everabot C++ accelerator for chunk_text (pybind11)";
  m.def("chunk_text_equiv", &chunk_text_equiv,
        py::arg("doc_id"), py::arg("text"),
        py::arg("chunk_tokens") = 400,
        py::arg("overlap_tokens") = 60);
}
```