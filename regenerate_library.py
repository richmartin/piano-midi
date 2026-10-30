#!/usr/bin/env python3

"""
regenerate_library.py

Static site generator for a MIDI file library.

Scans a directory of MIDI files, parses metadata, fetches data from Wikipedia,
and renders a static HTML website.

Usage:
  python3 regenerate_library.py -i /path/to/midi -t /path/to/templates -o /opt/www/midi
"""

import argparse
import csv
import json
import logging
import os
import pathlib
import re
import shutil
import subprocess
import unicodedata
import sys

import requests
import wikipedia
from datetime import datetime

# --- Configuration ---

# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

# Wikipedia cache file
WIKI_CACHE_FILE = ".wikipedia_cache.json"

# Regex patterns for filename parsing
# Ordered from most specific to least specific
FILENAME_REGEX_PATTERNS = [
    re.compile(
        r'^(?P<composer>.+?)\s+-\s+(?P<performer>.+?)\s+-\s+(?P<work>.+?)$'),
    re.compile(r'^(?P<composer>.+?)\s+-\s+(?P<work>.+?)$'),
    re.compile(r'^(?P<work>.+?)\s+by\s+(?P<composer>.+?)$'),
    re.compile(r'^(?P<work>.+?)\s+\((?P<composer>.+?)\)$'),
    re.compile(r'^(?P<composer>[^_]+)_(?P<work>.+)$'),
    re.compile(r'^(?P<work>.+?)\s+\((?P<composer>.+?)\)\s+(?P<performer>.+)\s+[^\s]+$'),
    re.compile(r'^(?P<work>.+)$')  # Fallback: captures the whole name as the work
]

# MediaWiki API endpoint for robust image fetching
WIKI_API_ENDPOINT = "https://en.wikipedia.org/w/api.php"

# Composer Aliases and Multi-Composer Mappings
COMPOSER_ALIASES = {
    # Multi-Composer
    "Liszt-Busoni": ["Franz Liszt", "Ferruccio Busoni"],
    "Bach-Busoni": ["Johann Sebastian Bach", "Ferruccio Busoni"],
    "Bach-Liszt": ["Johann Sebastian Bach", "Franz Liszt"],
    "Mozart-Volodos": ["Wolfgang Amadeus Mozart", "Arcadi Volodos"],
    "Schubert-Liszt": ["Franz Schubert", "Franz Liszt"],
    
    # Abbreviations / Normalization
    "Bthvn": ["Ludwig van Beethoven"],
    "Beethoven": ["Ludwig van Beethoven"],
    "Mozart": ["Wolfgang Amadeus Mozart"],
    "Bach": ["Johann Sebastian Bach"],
    "Chopin": ["Frédéric Chopin"],
    "Liszt": ["Franz Liszt"],
    "Rachmaninoff": ["Sergei Rachmaninoff"],
    "Rach": ["Sergei Rachmaninoff"],
    "Schubert": ["Franz Schubert"],
    "Schumann": ["Robert Schumann"],
    "Brahms": ["Johannes Brahms"],
    "Debussy": ["Claude Debussy"],
    "Ravel": ["Maurice Ravel"],
    "Prokofiev": ["Sergei Prokofiev"],
    "Scriabin": ["Alexander Scriabin"],
    "Tchaikovsky": ["Pyotr Ilyich Tchaikovsky"],
    "Mendelssohn": ["Felix Mendelssohn"],
    "Haydn": ["Joseph Haydn"],
    "Handel": ["George Frideric Handel"],
    "Vivaldi": ["Antonio Vivaldi"],
    "Scarlatti": ["Domenico Scarlatti"],
    "D Scarlatti": ["Domenico Scarlatti"],
}

# --- Utility Functions ---

def slugify(value):
    """
    Normalizes string, converts to lowercase, removes non-alpha characters,
    and converts spaces to hyphens.
    """
    value = unicodedata.normalize('NFKD', str(value)).encode(
        'ascii', 'ignore').decode('ascii')
    value = re.sub(r'[^\w\s-]', '', value).strip().lower()
    value = re.sub(r'[-\s]+', '-', value)
    return value


# --- Core Classes ---

# Wikipedia refuses (with an HTML page, which reads as "non-JSON") clients that don't say who they
# are; the wikipedia package sends no User-Agent of its own.
USER_AGENT = "PianoPi-library-generator/2.0 (https://piano.fam-mart.in)"   # Wikimedia wants a URL or address
wikipedia.set_user_agent(USER_AGENT)


class WikipediaClient:
    """
    Handles fetching and caching data from Wikipedia.
    """

    def __init__(self, cache_path):
        self.cache_path = pathlib.Path(cache_path)
        self.cache = self._load_cache()
        self.session = requests.Session()
        # Set a user-agent as required by MediaWiki API
        self.session.headers.update({
            'User-Agent': USER_AGENT
        })

    def _load_cache(self):
        """Loads the Wikipedia cache from a JSON file."""
        if self.cache_path.exists():
            logging.info(f"Loading Wikipedia cache from {self.cache_path}")
            with open(self.cache_path, 'r', encoding='utf-8') as f:
                try:
                    return json.load(f)
                except json.JSONDecodeError:
                    logging.warning("Cache file is corrupt. Starting fresh.")
                    return {}
        return {}

    def _save_cache(self):
        """Saves the current cache to a JSON file."""
        with open(self.cache_path, 'w', encoding='utf-8') as f:
            json.dump(self.cache, f, indent=2, ensure_ascii=False)

    def _get_page_image(self, page_title):
        """
        Fetches the main page image using the MediaWiki API,
        which is more reliable than the wikipedia library's.images property.
        """
        params = {
            "action": "query",
            "prop": "pageimages",
            "format": "json",
            "piprop": "original",
            "titles": page_title
        }
        try:
            response = self.session.get(WIKI_API_ENDPOINT, params=params)
            response.raise_for_status()
            data = response.json()
            pages = data.get("query", {}).get("pages", {})
            if not pages:
                return None
            
            # Get the first page_id
            page_id = next(iter(pages))
            page_data = pages[page_id]
            
            if "original" in page_data:
                return page_data["original"]["source"]
            
        except requests.RequestException as e:
            logging.warning(f"Failed to fetch image for {page_title}: {e}")
        return None

    def musician(self, name, kind):
        """{"image", "summary"} for the musician called `name` (kind: composer or pianist), or
        None. Tries the disambiguated page first ("John Ireland (composer)", not the actor) and
        keeps the first page that is about a musician. Cached, misses too."""
        key = f"musician:{kind}:{name}"
        if key in self.cache:
            return self.cache[key]
        found = None
        for title in (f"{name} ({kind})", name, f"{name} (musician)", f"{name} {kind}"):
            try:
                page = wikipedia.page(title, auto_suggest=False)
            except (wikipedia.exceptions.DisambiguationError, wikipedia.exceptions.PageError):
                continue
            except (ValueError, requests.RequestException) as e:
                logging.warning(f"Wikipedia lookup for {name!r} failed: {e}")
                return None                   # not cached: try again next time
            if MUSICIAN.search(page.summary[:600]):
                found = {"image": self._get_page_image(page.title), "summary": page.summary.split("\n")[0]}
                break
        self.cache[key] = found
        self._save_cache()
        return found

    def get_entity_data(self, name, entity_type="composer"):
        """
        Gets summary and image for an entity (composer, performer).
        Uses cache if available.
        """
        cache_key = f"{entity_type}:{name}"
        if cache_key in self.cache:
            logging.info(f"Cache HIT for: {name}")
            return self.cache[cache_key]

        logging.info(f"Cache MISS for: {name}. Fetching from Wikipedia...")
        
        # Try to find a valid Wikipedia page
        # Try to find a valid Wikipedia page
        search_terms = [
            f"{name} {entity_type}", # e.g. "John Ireland composer" - Most specific first
            name,
            f"{name} ({entity_type})",
            f"{name} (musician)"
        ]
        
        page = None
        page_title = None
        summary = f"No Wikipedia summary found for '{name}'."
        image_url = None # Placeholder image

        for term in search_terms:
            try:
                # auto_suggest=False prevents it from returning a different page
                page = wikipedia.page(term, auto_suggest=False)
                page_title = page.title
                summary = page.summary
                break  # Found a page
            except wikipedia.exceptions.DisambiguationError:
                continue # Try the next search term
            except wikipedia.exceptions.PageError:
                continue # Try the next search term
            except (ValueError, requests.RequestException) as e:
                # Wikipedia unreachable or refusing (it answers non-JSON to clients it blocks).
                # A missing portrait mustn't stop the library publishing; don't cache the miss.
                logging.warning(f"Wikipedia lookup for {name!r} failed: {e}")
                return {"name": name, "summary": "", "image_url": None}

        if page:
            # We found a page, now get the reliable image
            image_url = self._get_page_image(page_title)

        entity_data = {
            "name": name,
            "summary": summary,
            "image_url": image_url
        }

        # Save to cache and return
        self.cache[cache_key] = entity_data
        self._save_cache()
        return entity_data


class MidiLibraryGenerator:
    """
    Main class to orchestrate the static site generation.
    """

    def __init__(self, input_dir, template_dir, output_dir, ids_from=None):
        self.ids_from = ids_from
        self.input_dir = pathlib.Path(input_dir)
        self.output_dir = pathlib.Path(output_dir)

        # Ensure directories exist
        if not self.input_dir.is_dir():
            raise FileNotFoundError(f"Input directory not found: {input_dir}")
        # The web player (web/ beside this script). --template-dir is accepted and ignored: the
        # old Jinja templates are gone, and scripts that still pass it keep working.
        self.web_dir = pathlib.Path(__file__).resolve().parent / "web"
        if not (self.web_dir / "index.html").is_file():
            raise FileNotFoundError(f"The web player isn't at {self.web_dir}")
        
        # Setup output directories
        self.midi_files_dir = self.output_dir / "midi-files"

        # Initialize Wikipedia client
        cache_file = self.input_dir / WIKI_CACHE_FILE
        self.wiki_client = WikipediaClient(cache_file)
        
        # Generate Build ID
        self.build_id = datetime.now().strftime("%Y%m%d-%H%M%S")

    def _setup_output_dirs(self):
        """Creates output directories if they don't exist."""
        logging.info(f"Ensuring output directories exist at: {self.output_dir}")
        
        self.output_dir.mkdir(exist_ok=True)
        self.midi_files_dir.mkdir(exist_ok=True)

    # What the old site published and the web player replaces: the remote control, the device
    # selector's browser-MIDI pages, one page per composer, performer and file.
    OLD_SITE = ("static", "composers", "performers", "files", "remote.html")

    def _publish_web_player(self):
        """The web player: web/ as it is, its index.html stamped with the build (cache busting),
        and what the old site left behind removed."""
        for name in self.OLD_SITE:
            old = self.output_dir / name
            if old.is_dir():
                shutil.rmtree(old)
            elif old.exists():
                old.unlink()
        shutil.copytree(self.web_dir, self.output_dir, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("*.md", ".*"))
        index = self.output_dir / "index.html"
        index.write_text(index.read_text(encoding="utf-8").replace("__BUILD__", self.build_id), encoding="utf-8")

    def _stable_ids(self, files):
        """Each MIDI file's id, kept in ids.json beside the site so it never changes: the Pi
        mirrors files by id and never fetches an id twice, and favourites are kept by id. So a
        file added to the library gets a new id rather than shifting everyone else's. Ids of
        removed files are never reused.

        The first run (no ids.json yet) takes ids from an already published site if given one
        (--ids-from, matching files by content: the files may have changed since it was made),
        or else numbers the files in sorted order, as the generator always did."""
        path = self.output_dir / "ids.json"
        known = {}
        if path.exists():
            with open(path, encoding="utf-8") as f:
                known = json.load(f)
        rel = [f.relative_to(self.input_dir).as_posix() for f in files]
        if not known and self.ids_from:
            import hashlib
            digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()   # noqa: E731
            published = {}                     # content -> its ids (identical files can repeat)
            for p in sorted((pathlib.Path(self.ids_from) / "midi-files").glob("*.mid")):
                published.setdefault(digest(p), []).append(p.stem)
            for f, r in zip(files, rel):
                if published.get(digest(f)):
                    known[r] = published[digest(f)].pop(0)
            logging.info(f"ids from {self.ids_from}: {len(known)} of {len(rel)} files matched")
            # published ids that matched nothing stay retired, recorded so they're never reused
            for stem in sorted(s for stems in published.values() for s in stems):
                known[f"(retired)/{stem}"] = stem
        elif not known:
            known = {r: f"file-id-{i:06d}" for i, r in enumerate(rel)}
        used = [int(v.rsplit("-", 1)[1]) for v in known.values()]
        nxt = max(used) + 1 if used else 0
        for r in rel:
            if r not in known:
                known[r] = f"file-id-{nxt:06d}"
                logging.info(f"New file {r}: {known[r]}")
                nxt += 1
        self.output_dir.mkdir(exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(known, f, indent=1, ensure_ascii=False, sort_keys=True)
        return {f: known[r] for f, r in zip(files, rel)}

    def _find_midi_files(self):
        """Finds all.mid files in the input directory."""
        logging.info(f"Scanning for MIDI files in {self.input_dir}...")
        files = list(self.input_dir.rglob("*.mid"))
        files.sort() # Ensure stable order for ID generation
        logging.info(f"Found {len(files)} MIDI files.")
        return files

    def _parse_metadata(self, file_path):
        """
        Parses metadata from the filename using regex patterns.
        Returns a dict: {'work':..., 'composer':..., 'performer':...}
        """
        metadata = {}
        filename_stem = file_path.stem
        
        # Try regex patterns
        for pattern in FILENAME_REGEX_PATTERNS:
            match = pattern.match(filename_stem)
            if match:
                match_dict = match.groupdict()
                if 'work' not in metadata and 'work' in match_dict:
                    metadata['work'] = match_dict['work'].strip()
                if 'composer' not in metadata and 'composer' in match_dict:
                    metadata['composer'] = match_dict['composer'].strip()
                if 'performer' not in metadata and 'performer' in match_dict:
                    metadata['performer'] = match_dict['performer'].strip()
                
                # If we got the essentials, stop
                if 'work' in metadata and 'composer' in metadata:
                    break
        
        # Fallbacks
        if 'work' not in metadata:
            metadata['work'] = filename_stem # Use the filename as a last resort
        if 'composer' not in metadata:
            metadata['composer'] = "Unknown Composer"
        if 'performer' not in metadata:
            metadata['performer'] = "Unknown Performer"

        logging.info(f"For {file_path.name} got: {metadata}")
        return metadata

    def _extract_names_from_pdfs(self):
        """
        Scans input_dir for PDFs, converts to text, and extracts names with dates.
        Returns a mapping of {slug: Full Name (Dates)}.
        Used for both performers and composers to resolve short names.
        """
        mapping = {}
        pdfs = list(self.input_dir.rglob("*.pdf"))
        if not pdfs:
            logging.info("No PDF files found in input directory for name extraction.")
            return mapping

        logging.info(f"Found {len(pdfs)} PDF files. Extracting names...")
        
        # Regex to find "Name (YYYY-YYYY)" lines
        # Allows for spaces and different dash types (hyphen, en-dash)
        # We look for names embedded in the line, often preceded by multiple spaces in a table
        # We require the name to be at least 3 chars to avoid noise
        pattern = re.compile(r'([A-Za-z,.\' -]{3,})\s+\((\d{4}\s*[-–]\s*\d{4})\)')

        for pdf_file in pdfs:
            try:
                # Convert PDF to text using pdftotext
                result = subprocess.run(
                    ['pdftotext', '-layout', str(pdf_file), '-'], 
                    capture_output=True, 
                    text=True, 
                    check=True
                )
                content = result.stdout
                
                for line in content.splitlines():
                    line = line.strip()
                    # Use search instead of match to find it anywhere in the line
                    match = pattern.search(line)
                    if match:
                        full_name = match.group(1).strip()
                        dates = match.group(2).strip()
                        
                        # Clean up full_name (it might have captured preceding column text if not careful)
                        # In the table, it seems to be "   Name (Dates)   "
                        # Our regex ([A-Za-z,.' -]{3,}) is greedy, so it might eat backwards.
                        # But usually there are multiple spaces between columns.
                        # Let's rely on the fact that we stripped the line, but we are searching.
                        # Actually, if we use search, the greedy match might include the previous column if it's text.
                        # Example: "Title Name (Dates)" -> full_name = "Title Name"
                        # We should probably look for double spaces before the name if possible, or just trust the structure.
                        # The sample showed: "   Beach                Beach, Amy (1867-1944)   "
                        # The previous column ends with "Beach".
                        # If we match `\s{2,}([A-Za-z,.' -]+?)\s+\((\d{4}...)\)`, it forces a gap.
                        
                        # Let's try to be safer: split by double spaces and check each part?
                        # Or just use the regex we have but be careful.
                        # If I use `\s{2,}` it might fail if it's the first column.
                        # But in this PDF, the name seems to be in a middle column.
                        
                        # Let's try to clean the name: if it has multiple spaces inside, it might be "Title   Name".
                        # But names can have spaces.
                        # Let's stick to the simple regex for now but maybe trim it better.
                        
                        # If the name contains "   ", split and take the last part?
                        if "   " in full_name:
                            full_name = full_name.split("   ")[-1].strip()
                        if sum(ch.isalpha() for ch in full_name) < 3:
                            continue        # only punctuation or spaces before the dates

                        display_name = f"{full_name} ({dates})"
                        
                        # Heuristic for Short Name
                        if ',' in full_name:
                            # "Last, First" format
                            last_name = full_name.split(',')[0].strip()
                            # Reconstruct display name as "First Last" for consistency?
                            # Or keep as is? The mapping value is used for display.
                            # Let's keep the display name as found in PDF, or normalize it?
                            # If PDF says "Beach, Amy (1867-1944)", we probably want "Amy Beach (1867-1944)" for consistency?
                            # But let's just fix the mapping key first.
                        else:
                            # "First Last" format
                            parts = full_name.split()
                            last_name = parts[-1]
                            
                            if len(parts) > 1 and parts[-2].lower() == "d'albert":
                                 last_name = "d'Albert"
                            elif len(parts) > 1 and parts[-2].lower() == "von":
                                last_name = f"von {last_name}"

                        short_slug = slugify(last_name)
                        if short_slug not in mapping:
                            mapping[short_slug] = display_name
                        
                        # Also map the full slug
                        full_slug = slugify(full_name)
                        if full_slug not in mapping:
                            mapping[full_slug] = display_name
                            
                        if "d'albert" in full_name.lower():
                             mapping["dalbert"] = display_name

            except subprocess.CalledProcessError as e:
                logging.error(f"Failed to convert {pdf_file}: {e}")
            except Exception as e:
                logging.error(f"Error extracting from {pdf_file}: {e}")
        
        logging.info(f"Extracted {len(mapping)} names from PDFs.")
        return mapping

    def _load_pianist_mapping(self):
        """Loads the pianist mapping from CSV if it exists."""
        mapping = {}
        csv_path = self.input_dir / "pianists.csv"
        if csv_path.exists():
            logging.info(f"Loading pianist mapping from {csv_path}")
            with open(csv_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    mapping[row['Slug']] = row['DisplayName']
        return mapping

    @staticmethod
    def _copy(src, dst):
        """The bytes and the times, nothing else: copy2 also reads extended attributes, which an
        rclone mount (murdoch's gdrive) refuses with EIO."""
        shutil.copyfile(src, dst)
        st = src.stat()
        os.utime(dst, ns=(st.st_atime_ns, st.st_mtime_ns))

    def _smart_copy(self, src, dst):
        """
        Copies src to dst only if dst doesn't exist or is different.
        Checks file size and modification time.
        """
        if not dst.exists():
            self._copy(src, dst)
            return True
        
        src_stat = src.stat()
        dst_stat = dst.stat()
        
        # Check size
        if src_stat.st_size != dst_stat.st_size:
            self._copy(src, dst)
            return True
            
        # Check mtime (if src is newer than dst)
        if src_stat.st_mtime > dst_stat.st_mtime:
            self._copy(src, dst)
            return True
            
        return False

    def _format_display_name(self, full_name):
        """
        Formats a name as "Last, First (Dates)" for display.
        Input: "Amy Beach (1867-1944)" or "Amy Beach"
        Output: "Beach, Amy (1867-1944)" or "Beach, Amy"
        """
        # Check for dates
        match = re.match(r'^(.+?)\s+(\(\d{4}-\d{4}\))$', full_name)
        if match:
            name_part = match.group(1)
            dates_part = match.group(2)
        else:
            name_part = full_name
            dates_part = ""
            
        # Split name into parts
        parts = name_part.split()
        if len(parts) > 1:
            # Handle "von", "de", etc. if needed, but simple Last, First is usually ok
            last = parts[-1]
            first = " ".join(parts[:-1])
            formatted_name = f"{last}, {first}"
        else:
            formatted_name = name_part
            
        if dates_part:
            formatted_name += f" {dates_part}"
            
        return formatted_name

    def _resolve_composers(self, raw_name, pdf_mapping=None):
        """
        Resolves a raw composer name into a list of full names.
        Handles aliases, multi-composer strings (e.g. "Liszt-Busoni"),
        and PDF-based canonical name lookup.
        """
        resolved_names = []
        
        logging.debug(f"Resolving '{raw_name}'...")

        # 1. Check explicit aliases first (e.g. "Liszt-Busoni" -> ["Franz Liszt", "Ferruccio Busoni"])
        if raw_name in COMPOSER_ALIASES:
            val = COMPOSER_ALIASES[raw_name]
            names_to_process = val if isinstance(val, list) else [val]
        else:
            # 2. If no alias, check if it's a hyphenated name (e.g. "Alabieff-Liszt")
            # But be careful not to split double-barrelled surnames if they exist in aliases
            if '-' in raw_name:
                names_to_process = [part.strip() for part in raw_name.split('-')]
            else:
                names_to_process = [raw_name]

        # 3. Process each name (resolve against PDF mapping or Aliases)
        for name in names_to_process:
            # Check aliases again for individual parts (e.g. "Bthvn")
            if name in COMPOSER_ALIASES:
                val = COMPOSER_ALIASES[name]
                sub_names = val if isinstance(val, list) else [val]
                resolved_names.extend(sub_names)
                continue

            # Check PDF mapping (Canonical Name Database)
            name_slug = slugify(name)
            if pdf_mapping and name_slug in pdf_mapping:
                full_string = pdf_mapping[name_slug]
                logging.debug(f"  Found in PDF mapping: {name_slug} -> {full_string}")
                
                # Extract name part: "Name (Dates)" -> "Name"
                match = re.match(r'^(.+?)\s+\(\d{4}-\d{4}\)$', full_string)
                if match:
                    resolved_names.append(match.group(1))
                else:
                    resolved_names.append(full_string)
            else:
                logging.debug(f"  NOT found in PDF mapping: {name_slug}")
                # No resolution found, use as is
                resolved_names.append(name)
        
        # Deduplicate and return
        return list(dict.fromkeys(resolved_names))

    def build_data_model(self):
        """
        Builds the complete master data model for the entire library.
        """
        # FIX 2: Create directories *before* trying to copy files [3]
        self._setup_output_dirs()
        
        logging.info("Building data model...")
        midi_files = self._find_midi_files()
        ids = self._stable_ids(midi_files)
        
        # Load mappings from CSV and PDFs
        csv_mapping = self._load_pianist_mapping()
        # Use the new generalized extraction for both performers and composers
        pdf_name_mapping = self._extract_names_from_pdfs()
        
        # Merge mappings for performers (PDFs take precedence)
        pianist_mapping = {**csv_mapping, **pdf_name_mapping}

        model = {
            "composers": {},
            "performers": {},
            "files": {},
            "playlists": {}
        }

        for file_path in midi_files:
            file_id = ids[file_path]
            logging.info(f"Processing {file_id}: {file_path.name}")
            
            # 1. Parse metadata
            metadata = self._parse_metadata(file_path)
            
            # 2. Get/Create Composer(s)
            raw_composer_name = metadata['composer']
            # Pass the PDF mapping to help resolve short names
            resolved_composers = self._resolve_composers(raw_composer_name, pdf_name_mapping)
            
            composer_slugs = []
            for composer_name in resolved_composers:
                composer_slug = slugify(composer_name)
                composer_slugs.append(composer_slug)
                
                if composer_slug not in model['composers']:
                    # Format for display: "Last, First (Dates)"
                    display_name = self._format_display_name(composer_name)
                    
                    # Clean name for Wiki search (remove dates)
                    # "Amy Beach (1867-1944)" -> "Amy Beach"
                    search_name = re.sub(r'\s+\(\d{4}-\d{4}\)', '', composer_name)
                    
                    wiki_data = self.wiki_client.get_entity_data(search_name, "composer")
                    
                    model['composers'][composer_slug] = {
                        "name": display_name, # Use formatted name for UI
                        "slug": composer_slug,
                        "page_url": f"/composers/{composer_slug}.html",
                        "works": [],
                        **wiki_data
                    }
                model['composers'][composer_slug]['works'].append(file_id)
            
            # Use the first composer as the primary one for the file entry
            primary_composer_slug = composer_slugs[0] if composer_slugs else "unknown"

            # 3. Get/Create Performer
            performer_name = metadata['performer']
            performer_slug = slugify(performer_name)
            
            # Use full name from mapping if available
            if performer_slug in pianist_mapping:
                performer_name = pianist_mapping[performer_slug]
            
            if performer_slug not in model['performers']:
                # Format for display: "Last, First (Dates)"
                display_name = self._format_display_name(performer_name)
                
                # Clean name for Wiki search (remove dates)
                search_name = re.sub(r'\s+\(\d{4}-\d{4}\)', '', performer_name)
                
                wiki_data = self.wiki_client.get_entity_data(search_name, "performer")
                model['performers'][performer_slug] = {
                    "name": display_name,
                    "slug": performer_slug,
                    "page_url": f"/performers/{performer_slug}.html",
                    "works": [],
                    **wiki_data
                }
            model['performers'][performer_slug]['works'].append(file_id)

            # 4. Copy MIDI file to output (Smart Copy)
            output_midi_path = self.midi_files_dir / f"{file_id}.mid"
            if self._smart_copy(file_path, output_midi_path):
                logging.info(f"Copied/Updated: {file_path.name} -> {output_midi_path.name}")
            else:
                logging.debug(f"Skipped (unchanged): {file_path.name}")

            # 5. Add File entry
            model['files'][file_id] = {
                "id": file_id,
                "title": metadata['work'],
                "composer_slug": primary_composer_slug,
                "performer_slug": performer_slug,
                "page_url": f"/files/{file_id}.html",
                "midi_url": f"/midi-files/{file_id}.mid" # Relative URL
            }

        logging.info("Data model built. Generating playlists...")
        
        # 6. Cleanup Orphaned Files
        generated_files = set(f"{fid}.mid" for fid in model['files'].keys())
        existing_files = set(f.name for f in self.midi_files_dir.glob("*.mid"))
        orphaned_files = existing_files - generated_files
        
        if orphaned_files:
            logging.info(f"Removing {len(orphaned_files)} orphaned files from output...")
            for filename in orphaned_files:
                (self.midi_files_dir / filename).unlink()

        # 7. Generate Playlists
        for slug, composer in model['composers'].items():
            playlist_key = f"composer-{slug}"
            model['playlists'][playlist_key] = [
                {
                    "url": model['files'][file_id]['midi_url'],
                    "title": model['files'][file_id]['title']
                }
                for file_id in composer['works']
            ]
            
        for slug, performer in model['performers'].items():
            playlist_key = f"performer-{slug}"
            model['playlists'][playlist_key] = [
                {
                    "url": model['files'][file_id]['midi_url'],
                    "title": model['files'][file_id]['title']
                }
                for file_id in performer['works']
            ]

        logging.info("Data model and playlists complete.")
        return model

    def render_static_site(self, model):
        """The web player, library.json (the tidied catalogue, which the Pi mirrors) and
        people.json (portraits and a paragraph from Wikipedia, for the player's pages)."""
        logging.info("Publishing the web player...")
        self._publish_web_player()

        # library.json, built from the model, then tidied
        library_data = {
            "composers": []
        }
        
        # Sort composers by name
        sorted_composers = sorted(model['composers'].values(), key=lambda x: x['name'])
        
        for comp in sorted_composers:
            comp_entry = {
                "name": comp['name'],
                "slug": comp['slug'],
                "works": []
            }
            # Get works for this composer
            for file_id in comp['works']:
                file_data = model['files'][file_id]
                comp_entry['works'].append({
                    "id": file_id,
                    "title": file_data['title'],
                    "url": file_data['midi_url']
                })
            # Sort works by title
            comp_entry['works'].sort(key=lambda x: x['title'])
            library_data['composers'].append(comp_entry)

        # The catalogue tidied (pianod/pianod/catalogue.py): canonical composers with dates and
        # eras, the pianists and roll systems in the roll titles, clean titles, lengths. It keeps
        # every v1 field, so older players still read it.
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "pianod"))
        from pianod import catalogue, smf

        def duration(work_id):
            try:
                events, _ = smf.load(str(self.midi_files_dir / f"{work_id}.mid"))
                return round(events[-1].time, 1) if events else None
            except (OSError, ValueError, IndexError, EOFError):
                return None

        library_data = catalogue.tidy(library_data, duration=duration)
        with open(self.output_dir / "library.json", 'w', encoding='utf-8') as f:
            json.dump(library_data, f, ensure_ascii=False)
        with open(self.output_dir / "people.json", 'w', encoding='utf-8') as f:
            json.dump(self._people(library_data), f, ensure_ascii=False)

        logging.info("Site generation complete.")

    def _people(self, library):
        return people_from(library, self.wiki_client.musician)


# A Wikipedia summary is shown only if it's about a musician: a lookup by name alone can land
# on a namesake (a footballer, a bishop) and a wrong biography is worse than none.
MUSICIAN = re.compile(r"\b(composer|pianist|musician|organist|harpsichordist|conductor|songwriter)", re.I)


def people_from(library, lookup):
    """{"composers": {slug: {image, summary}}, "pianists": {...}} for the tidied catalogue,
    looked up by each person's canonical full name. `lookup(name, kind)` -> {"image", "summary"} or None (WikipediaClient.musician)."""
    out = {"composers": {}, "pianists": {}}
    for kind, items, wiki_kind in (("composers", library.get("composers", []), "composer"),
                                   ("pianists", library.get("pianists", []), "pianist")):
        for p in items:
            if p.get("slug") in (None, "", "unknown"):
                continue
            found = lookup(p["name"], wiki_kind)
            if found:
                out[kind][p["slug"]] = found
    return out


# --- Main Execution ---

def main():
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(
        description="Static site generator for a MIDI file library.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        '-i', '--input-dir',
        required=True,
        help="Path to the root directory of your MIDI file library."
    )
    parser.add_argument(
        '-t', '--template-dir',
        help="Ignored: the web player is web/ beside this script (kept so older scripts still run)"
    )
    parser.add_argument(
        '-o', '--output-dir',
        required=True,
        help="Path to the directory where the static site will be generated."
    )
    parser.add_argument(
        '--ids-from',
        help="On the first run (no ids.json in the output yet): an already published site whose "
             "midi-files/ keep their ids, matched by content. Players mirror files by id."
    )
    parser.add_argument(
        '--debug',
        action='store_true',
        help="Enable debug logging."
    )
    
    args = parser.parse_args()

    # Configure logging based on flag
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.getLogger().setLevel(log_level)

    try:
        generator = MidiLibraryGenerator(
            args.input_dir,
            args.template_dir,
            args.output_dir,
            ids_from=args.ids_from,
        )
        
        # 1. Build the data model
        #    (This function now also creates the output dirs)
        model = generator.build_data_model()
        
        # 2. Render the site
        generator.render_static_site(model)
        
        logging.info(f"Successfully generated site at: {args.output_dir}")

    except FileNotFoundError as e:
        logging.error(f"Error: {e}")
        exit(1)
    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}", exc_info=True)
        exit(1)

if __name__ == "__main__":
    main()
