package chunker

import (
	"strings"
	"strconv"
)

type Req struct {
	DocID         string `json:"doc_id"`
	Text          string `json:"text"`
	ChunkTokens   int    `json:"chunk_tokens"`
	OverlapTokens int    `json:"overlap_tokens"`
}

type Chunk struct {
	ChunkID string `json:"chunk_id"`
	Ord     int    `json:"ord"`
	Body    string `json:"body"`
	Tokens  int    `json:"tokens"`
}

func estimateTokens(s string) int {
	n := len(s)
	t := (n + 3) / 4
	if t < 1 {
		return 1
	}
	return t
}

func ChunkTextEquiv(req Req) []Chunk {
	words := strings.Fields(req.Text)
	step := req.ChunkTokens - req.OverlapTokens
	if step < 1 {
		step = 1
	}
	var out []Chunk
	i := 0
	ord := 0
	for i < len(words) {
		end := i + req.ChunkTokens
		if end > len(words) {
			end = len(words)
		}
		if i >= end {
			break
		}
		body := strings.Join(words[i:end], " ")
		if len(body) > 0 {
			out = append(out, Chunk{
				ChunkID: req.DocID + ":" + strconv.Itoa(ord),
				Ord:     ord,
				Body:    body,
				Tokens:  estimateTokens(body),
			})
			ord++
		}
		i += step
	}
	return out
}
