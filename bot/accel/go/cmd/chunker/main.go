package main

import (
	"encoding/json"
	chunker "everabot/chunker/chunker"
	"log"
	"net/http"
	"os"
	"time"
)

func main() {
	addr := ":8765"
	if v := os.Getenv("EVERABOT_GO_CHUNK_ADDR"); v != "" {
		addr = v
	}
	mux := http.NewServeMux()

	mux.HandleFunc("POST /chunk", func(w http.ResponseWriter, r *http.Request) {
		defer r.Body.Close()
		var req chunker.Req
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, "bad json", http.StatusBadRequest)
			return
		}
		ch := chunker.ChunkTextEquiv(req)
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(ch)
	})

	s := &http.Server{
		Addr:              addr,
		Handler:           mux,
		ReadHeaderTimeout: 5 * time.Second,
	}
	log.Printf("everabot-chunk-go listening on %s", addr)
	log.Fatal(s.ListenAndServe())
}
