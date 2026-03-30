package main

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"github.com/spf13/cobra"
)

const (
	deezerAPI  = "https://api.deezer.com"
	ytcURL     = "https://ytc.mba.sh"
	userAgent  = "anysong/2.0"
	maxRetries = 2
)

// DeezerResponse is the top-level search response
type DeezerResponse struct {
	Data []DeezerTrack `json:"data"`
}

// DeezerTrack is a single Deezer search result
type DeezerTrack struct {
	Title    string       `json:"title"`
	Duration int          `json:"duration"`
	Preview  string       `json:"preview"`
	Artist   DeezerArtist `json:"artist"`
	Album    DeezerAlbum  `json:"album"`
}

// DeezerArtist is the artist info in a Deezer result
type DeezerArtist struct {
	Name string `json:"name"`
}

// DeezerAlbum is the album info in a Deezer result
type DeezerAlbum struct {
	Title string `json:"title"`
}

var (
	defaultDir string
	cookiesDir string
)

func init() {
	home, _ := os.UserHomeDir()
	defaultDir = filepath.Join(home, "music")
	cookiesDir = filepath.Join(home, ".anysong")
}

func main() {
	rootCmd := &cobra.Command{
		Use:   "anysong",
		Short: "🎵 Download any song as a properly named MP3",
		Long:  "Download any song as a properly named MP3. One command.",
	}

	var outputDir string
	var previewOK bool
	var pick bool

	downloadCmd := &cobra.Command{
		Use:   "download [query]",
		Short: "Download a song",
		Args:  cobra.ExactArgs(1),
		Run: func(cmd *cobra.Command, args []string) {
			query := args[0]
			dir := outputDir
			if dir == "" {
				dir = defaultDir
			}
			os.MkdirAll(dir, 0755)
			downloadSong(query, dir, pick, previewOK)
		},
	}
	downloadCmd.Flags().StringVarP(&outputDir, "dir", "d", "", "Output directory (default ~/music)")
	downloadCmd.Flags().BoolVarP(&pick, "pick", "p", false, "Show results and let you pick")
	downloadCmd.Flags().BoolVar(&previewOK, "preview-ok", false, "Accept 30s Deezer preview as fallback")

	var searchLimit int
	searchCmd := &cobra.Command{
		Use:   "search [query]",
		Short: "Search for songs (Deezer metadata)",
		Args:  cobra.ExactArgs(1),
		Run: func(cmd *cobra.Command, args []string) {
			searchSongs(args[0], searchLimit)
		},
	}
	searchCmd.Flags().IntVarP(&searchLimit, "limit", "n", 10, "Number of results")

	batchCmd := &cobra.Command{
		Use:   "batch [file]",
		Short: "Download multiple songs from a text file",
		Args:  cobra.ExactArgs(1),
		Run: func(cmd *cobra.Command, args []string) {
			dir := outputDir
			if dir == "" {
				dir = defaultDir
			}
			os.MkdirAll(dir, 0755)
			batchDownload(args[0], dir, previewOK)
		},
	}
	batchCmd.Flags().StringVarP(&outputDir, "dir", "d", "", "Output directory (default ~/music)")
	batchCmd.Flags().BoolVar(&previewOK, "preview-ok", false, "Accept 30s Deezer preview as fallback")

	cookiesCmd := &cobra.Command{
		Use:   "setup-cookies",
		Short: "Set up YouTube cookies",
		Run: func(cmd *cobra.Command, args []string) {
			setupCookies()
		},
	}

	rootCmd.AddCommand(downloadCmd, searchCmd, batchCmd, cookiesCmd)

	// Allow bare `anysong "query"` as shortcut for download
	rootCmd.Run = func(cmd *cobra.Command, args []string) {
		if len(args) == 1 {
			dir := outputDir
			if dir == "" {
				dir = defaultDir
			}
			os.MkdirAll(dir, 0755)
			downloadSong(args[0], dir, false, false)
		} else {
			cmd.Help()
		}
	}
	rootCmd.Args = cobra.ArbitraryArgs

	if err := rootCmd.Execute(); err != nil {
		os.Exit(1)
	}
}

// --- Deezer ---

func deezerSearch(query string, limit int) []DeezerTrack {
	u := fmt.Sprintf("%s/search?q=%s&limit=%d", deezerAPI, url.QueryEscape(query), limit)
	resp, err := httpGet(u)
	if err != nil {
		fmt.Printf("  \033[2mDeezer search failed: %v\033[0m\n", err)
		return nil
	}
	defer resp.Body.Close()

	var result DeezerResponse
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil
	}
	return result.Data
}

// --- Filename ---

func sanitize(name string) string {
	re := regexp.MustCompile(`[<>:"/\\|?*]`)
	name = re.ReplaceAllString(name, "")
	name = regexp.MustCompile(`\s+`).ReplaceAllString(strings.TrimSpace(name), "_")
	name = strings.ToLower(name)
	name = strings.Trim(name, "_.")
	return name
}

func buildFilename(artist, title string) string {
	return fmt.Sprintf("%s_by_%s.mp3", sanitize(title), sanitize(artist))
}

func formatDuration(seconds int) string {
	if seconds == 0 {
		return "?"
	}
	return fmt.Sprintf("%d:%02d", seconds/60, seconds%60)
}

// --- Cookies ---

func cookiesFile() string {
	return filepath.Join(cookiesDir, "cookies.txt")
}

func ensureCookies() string {
	os.MkdirAll(cookiesDir, 0755)
	cf := cookiesFile()

	// Check local fresh cookies
	if info, err := os.Stat(cf); err == nil && info.Size() > 100 {
		age := time.Since(info.ModTime()).Hours()
		if age < 24 {
			return cf
		}
	}

	// Try fetching from central service
	func() {
		resp, err := httpGet(ytcURL + "/health")
		if err != nil {
			return
		}
		defer resp.Body.Close()

		var health map[string]interface{}
		if err := json.NewDecoder(resp.Body).Decode(&health); err != nil {
			return
		}
		if avail, ok := health["cookies_available"].(bool); !ok || !avail {
			return
		}

		resp2, err := httpGet(ytcURL + "/cookies.txt")
		if err != nil {
			return
		}
		defer resp2.Body.Close()

		data, err := io.ReadAll(resp2.Body)
		if err != nil || len(data) < 100 {
			return
		}

		os.WriteFile(cf, data, 0644)
		fmt.Println("  \033[2m🍪 Cookies refreshed from ytc.mba.sh\033[0m")
	}()

	// Fall back to stale local
	if info, err := os.Stat(cf); err == nil && info.Size() > 100 {
		return cf
	}
	return ""
}

// --- Download ---

type downloadResult struct {
	success bool
	source  string
	quality string
}

type source struct {
	name     string
	template string
}

var sources = []source{
	{"youtube", "ytsearch1:%s"},
	{"soundcloud", "scsearch1:%s"},
}

func findYtdlp() string {
	if p, err := exec.LookPath("yt-dlp"); err == nil {
		return p
	}
	return "yt-dlp"
}

func tryDownload(searchQuery, outputPath, sourceTemplate, sourceName string) bool {
	ytdlp := findYtdlp()

	outTemplate := strings.TrimSuffix(outputPath, ".mp3") + ".%(ext)s"
	cmd := []string{
		ytdlp,
		"--extract-audio",
		"--audio-format", "mp3",
		"--audio-quality", "0",
		"--max-downloads", "1",
		"--no-playlist",
		"--output", outTemplate,
		"--print", "after_move:filepath",
	}

	if strings.Contains(sourceTemplate, "search") {
		cmd = append(cmd, "--match-filter", "duration<=600")
	}

	searchURL := fmt.Sprintf(sourceTemplate, searchQuery)
	cmd = append(cmd, searchURL)

	if sourceName == "youtube" {
		if cookies := ensureCookies(); cookies != "" {
			cmd = append(cmd, "--cookies", cookies)
		}
	}

	env := os.Environ()
	home, _ := os.UserHomeDir()
	denoPath := filepath.Join(home, ".deno", "bin")
	if _, err := os.Stat(denoPath); err == nil {
		for i, e := range env {
			if strings.HasPrefix(e, "PATH=") {
				env[i] = fmt.Sprintf("PATH=%s:%s", denoPath, strings.TrimPrefix(e, "PATH="))
				break
			}
		}
	}

	proc := exec.Command(cmd[0], cmd[1:]...)
	proc.Env = env
	out, err := proc.CombinedOutput()

	exitCode := 0
	if err != nil {
		if exitErr, ok := err.(*exec.ExitError); ok {
			exitCode = exitErr.ExitCode()
		} else {
			return false
		}
	}

	if exitCode != 0 && exitCode != 101 { // 101 = max downloads reached
		return false
	}

	// Check if file exists at expected path
	if fileExists(outputPath) {
		return true
	}

	// Check printed filepath
	lines := strings.Split(strings.TrimSpace(string(out)), "\n")
	if len(lines) > 0 {
		actual := lines[len(lines)-1]
		if fileExists(actual) {
			os.Rename(actual, outputPath)
			return true
		}
	}

	// Check for any new mp3 in the directory
	dir := filepath.Dir(outputPath)
	entries, _ := os.ReadDir(dir)
	var newest string
	var newestTime time.Time
	for _, e := range entries {
		if strings.HasSuffix(e.Name(), ".mp3") {
			info, _ := e.Info()
			if info != nil && info.ModTime().After(newestTime) {
				newest = filepath.Join(dir, e.Name())
				newestTime = info.ModTime()
			}
		}
	}
	if newest != "" && newest != outputPath {
		os.Rename(newest, outputPath)
		return true
	}

	return false
}

func tryDeezerPreview(previewURL, outputPath string) bool {
	if previewURL == "" {
		return false
	}
	resp, err := httpGet(previewURL)
	if err != nil {
		return false
	}
	defer resp.Body.Close()

	data, err := io.ReadAll(resp.Body)
	if err != nil || len(data) < 10000 {
		return false
	}

	os.MkdirAll(filepath.Dir(outputPath), 0755)
	return os.WriteFile(outputPath, data, 0644) == nil
}

func doDownload(ytQuery, outputPath, simpleQuery, previewURL string) downloadResult {
	if simpleQuery == "" {
		simpleQuery = ytQuery
	}

	for _, src := range sources {
		fmt.Printf("  \033[2mTrying %s...\033[0m\n", src.name)
		q := ytQuery
		if src.name != "youtube" {
			q = simpleQuery
		}
		if tryDownload(q, outputPath, src.template, src.name) {
			info, _ := os.Stat(outputPath)
			if info != nil && info.Size() < 100_000 {
				fmt.Printf("  \033[2m%s: got a short clip, trying next...\033[0m\n", src.name)
				os.Remove(outputPath)
				continue
			}
			return downloadResult{true, src.name, "full"}
		}
		fmt.Printf("  \033[2m%s: failed, trying next...\033[0m\n", src.name)
	}

	if previewURL != "" {
		fmt.Println("  \033[2mTrying deezer preview (30s)...\033[0m")
		if tryDeezerPreview(previewURL, outputPath) {
			return downloadResult{true, "deezer", "30s preview"}
		}
	}

	return downloadResult{false, "", ""}
}

// --- Commands ---

func downloadSong(query, dir string, pick, previewOK bool) {
	fmt.Printf("\n╭─ 🎵 anysong — %s\n", query)

	results := deezerSearch(query, 5)

	if len(results) == 0 {
		fmt.Println("⚠ No metadata found. Downloading with raw query...")
		filename := sanitize(query) + ".mp3"
		outputPath := filepath.Join(dir, filename)

		result := doDownload(query, outputPath, "", "")
		if result.success {
			info, _ := os.Stat(outputPath)
			sizeMB := float64(info.Size()) / (1024 * 1024)
			fmt.Printf("\n\033[32m✓\033[0m %s (%.1f MB) via %s\n", outputPath, sizeMB, result.source)
		} else {
			fmt.Printf("\n\033[31m✗ Could not download: %s\033[0m\n", query)
		}
		return
	}

	track := results[0]

	if pick && len(results) > 1 {
		fmt.Println("\n  # │ Title                │ Artist          │ Album           │ Duration")
		fmt.Println("  ──┼──────────────────────┼─────────────────┼─────────────────┼────────")
		for i, t := range results {
			fmt.Printf("  %d │ %-20s │ %-15s │ %-15s │ %s\n",
				i+1,
				truncate(t.Title, 20),
				truncate(t.Artist.Name, 15),
				truncate(t.Album.Title, 15),
				formatDuration(t.Duration),
			)
		}
		fmt.Print("\n  Pick # [1]: ")
		var choice int
		fmt.Scan(&choice)
		if choice >= 1 && choice <= len(results) {
			track = results[choice-1]
		}
	}

	title := track.Title
	artist := track.Artist.Name
	album := track.Album.Title
	duration := track.Duration
	previewURL := ""
	if previewOK {
		previewURL = track.Preview
	}

	fmt.Printf("  \033[36m%s\033[0m by \033[32m%s\033[0m", title, artist)
	if album != "" {
		fmt.Printf(" — \033[33m%s\033[0m", album)
	}
	fmt.Printf(" \033[2m(%s)\033[0m\n", formatDuration(duration))

	filename := buildFilename(artist, title)
	outputPath := filepath.Join(dir, filename)

	if fileExists(outputPath) {
		info, _ := os.Stat(outputPath)
		sizeMB := float64(info.Size()) / (1024 * 1024)
		fmt.Printf("\033[33mAlready have it:\033[0m %s (%.1f MB)\n", outputPath, sizeMB)
		return
	}

	ytQuery := fmt.Sprintf("%s %s official audio", artist, title)
	simpleQuery := fmt.Sprintf("%s %s", artist, title)

	result := doDownload(ytQuery, outputPath, simpleQuery, previewURL)

	if result.success {
		info, _ := os.Stat(outputPath)
		sizeMB := float64(info.Size()) / (1024 * 1024)
		qualityNote := ""
		if result.quality != "full" {
			qualityNote = fmt.Sprintf(" \033[33m(%s)\033[0m", result.quality)
		}
		fmt.Printf("\n\033[32m✓\033[0m %s (%.1f MB)%s via %s\n", outputPath, sizeMB, qualityNote, result.source)
	} else {
		fmt.Printf("\n\033[31m✗ Could not download: %s by %s\033[0m\n", title, artist)
		fmt.Println("\033[2mNo cookies available. Run: anysong setup-cookies\033[0m")
	}
}

func searchSongs(query string, limit int) {
	fmt.Printf("\n🔍 Searching '%s'...\n\n", query)

	results := deezerSearch(query, limit)
	if len(results) == 0 {
		fmt.Println("\033[31mNo results.\033[0m")
		return
	}

	fmt.Println("  # │ Title                │ Artist          │ Album           │ Duration")
	fmt.Println("  ──┼──────────────────────┼─────────────────┼─────────────────┼────────")
	for i, t := range results {
		fmt.Printf("  %d │ %-20s │ %-15s │ %-15s │ %s\n",
			i+1,
			truncate(t.Title, 20),
			truncate(t.Artist.Name, 15),
			truncate(t.Album.Title, 15),
			formatDuration(t.Duration),
		)
	}
	fmt.Println()
}

func batchDownload(file, dir string, previewOK bool) {
	data, err := os.ReadFile(file)
	if err != nil {
		fmt.Printf("\033[31mCannot read %s: %v\033[0m\n", file, err)
		return
	}

	var lines []string
	for _, line := range strings.Split(string(data), "\n") {
		line = strings.TrimSpace(line)
		if line != "" && !strings.HasPrefix(line, "#") {
			lines = append(lines, line)
		}
	}

	fmt.Printf("\033[1mDownloading %d songs → %s\033[0m\n\n", len(lines), dir)

	ok, previews := 0, 0
	var failed []string

	for i, line := range lines {
		fmt.Printf("\033[2m(%d/%d)\033[0m %s\n", i+1, len(lines), line)

		results := deezerSearch(line, 1)
		var filename, ytQuery, simpleQ, previewURL string

		if len(results) > 0 {
			t := results[0]
			filename = buildFilename(t.Artist.Name, t.Title)
			ytQuery = fmt.Sprintf("%s %s official audio", t.Artist.Name, t.Title)
			simpleQ = fmt.Sprintf("%s %s", t.Artist.Name, t.Title)
			if previewOK {
				previewURL = t.Preview
			}
		} else {
			filename = sanitize(line) + ".mp3"
			ytQuery = line
			simpleQ = line
		}

		outputPath := filepath.Join(dir, filename)
		if fileExists(outputPath) {
			fmt.Printf("  \033[33mexists\033[0m %s\n", filename)
			ok++
			continue
		}

		result := doDownload(ytQuery, outputPath, simpleQ, previewURL)
		if result.success {
			if result.quality == "30s preview" {
				fmt.Printf("  \033[33m⚠ preview only\033[0m %s\n", filename)
				previews++
			} else {
				fmt.Printf("  \033[32m✓\033[0m %s via %s\n", filename, result.source)
			}
			ok++
		} else {
			fmt.Printf("  \033[31m✗\033[0m failed\n")
			failed = append(failed, line)
		}
	}

	fmt.Printf("\n\033[1m%d/%d downloaded\033[0m", ok, len(lines))
	if previews > 0 {
		fmt.Printf(" \033[33m(%d previews only)\033[0m", previews)
	}
	fmt.Println()
	if len(failed) > 0 {
		fmt.Println("\033[31mFailed:\033[0m")
		for _, f := range failed {
			fmt.Printf("  - %s\n", f)
		}
	}
}

func setupCookies() {
	fmt.Println("\n╭─ 🍪 YouTube Cookie Setup")
	fmt.Println("│")
	fmt.Println("│  anysong automatically fetches cookies from ytc.mba.sh.")
	fmt.Println("│  If that fails, it uses local cookies from ~/.anysong/cookies.txt")
	fmt.Println("│")

	// Check central
	resp, err := httpGet(ytcURL + "/health")
	if err == nil {
		defer resp.Body.Close()
		var health map[string]interface{}
		if json.NewDecoder(resp.Body).Decode(&health) == nil {
			if avail, ok := health["cookies_available"].(bool); ok && avail {
				age, _ := health["cookies_age_hours"].(float64)
				fmt.Printf("│  \033[32m✓ Central cookies available\033[0m (age: %.0fh)\n", age)
			} else {
				fmt.Println("│  \033[33m⚠ No cookies on central server yet\033[0m")
			}
		}
	} else {
		fmt.Println("│  \033[31m✗ Could not reach ytc.mba.sh\033[0m")
	}

	// Check local
	cf := cookiesFile()
	if info, err := os.Stat(cf); err == nil && info.Size() > 100 {
		age := time.Since(info.ModTime()).Hours()
		fmt.Printf("│  \033[32m✓ Local cookies found\033[0m (%d bytes, %.0fh old)\n", info.Size(), age)
	} else {
		fmt.Printf("│  \033[2mNo local cookies at %s\033[0m\n", cf)
	}

	fmt.Println("│")
	fmt.Println("│  To add cookies:")
	fmt.Println("│    1. On a machine with Chrome + YouTube logged in:")
	fmt.Println("│       yt-dlp --cookies-from-browser chrome --cookies cookies.txt 'https://youtube.com'")
	fmt.Println("│")
	fmt.Println("│    2. Upload to central server:")
	fmt.Println("│       scp cookies.txt cbot@server:~/ytc/cookies.txt")
	fmt.Println("│")
	fmt.Println("│    3. Or keep local only:")
	fmt.Println("│       cp cookies.txt ~/.anysong/cookies.txt")
	fmt.Println("╰─")
}

// --- Helpers ---

func httpGet(url string) (*http.Response, error) {
	client := &http.Client{Timeout: 10 * time.Second}
	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", userAgent)
	return client.Do(req)
}

func fileExists(path string) bool {
	info, err := os.Stat(path)
	return err == nil && !info.IsDir()
}

func truncate(s string, max int) string {
	if len(s) <= max {
		return s
	}
	return s[:max-1] + "…"
}
