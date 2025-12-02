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
import json
import logging
import os
import pathlib
import re
import shutil
import unicodedata
import requests
import mido
import wikipedia
from jinja2 import Environment, FileSystemLoader

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
            'User-Agent': 'MIDI-Library-Generator/1.0 (https://example.com; cool-user@example.com)'
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
        search_terms = [
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

    def __init__(self, input_dir, template_dir, output_dir):
        self.input_dir = pathlib.Path(input_dir)
        self.template_dir = pathlib.Path(template_dir)
        self.output_dir = pathlib.Path(output_dir)

        # Ensure directories exist
        if not self.input_dir.is_dir():
            raise FileNotFoundError(f"Input directory not found: {input_dir}")
        if not self.template_dir.is_dir():
            raise FileNotFoundError(f"Template directory not found: {template_dir}")
        
        # Setup output directories
        self.static_dir = self.output_dir / "static"
        self.midi_files_dir = self.output_dir / "midi-files"
        self.composers_dir = self.output_dir / "composers"
        self.performers_dir = self.output_dir / "performers"
        self.files_dir = self.output_dir / "files"

        # Initialize Wikipedia client
        cache_file = self.input_dir / WIKI_CACHE_FILE
        self.wiki_client = WikipediaClient(cache_file)
        
        # Initialize Jinja2
        self.jinja_env = Environment(
            loader=FileSystemLoader(self.template_dir),
            autoescape=True
        )

    def _setup_output_dirs(self):
        """Creates a clean set of output directories."""
        logging.info(f"Cleaning output directory: {self.output_dir}")
        if self.output_dir.exists():
            shutil.rmtree(self.output_dir)
        
        logging.info("Creating output directories...")
        self.output_dir.mkdir()
        self.static_dir.mkdir()
        self.midi_files_dir.mkdir()
        self.composers_dir.mkdir()
        self.performers_dir.mkdir()
        self.files_dir.mkdir()

    def _copy_static_assets(self):
        """Copies static assets (CSS, JS) from template dir to output dir."""
        template_static_dir = self.template_dir / "static"
        if template_static_dir.is_dir():
            logging.info("Copying static assets...")
            shutil.copytree(template_static_dir, self.static_dir, dirs_exist_ok=True)

    def _find_midi_files(self):
        """Finds all.mid files in the input directory."""
        logging.info(f"Scanning for MIDI files in {self.input_dir}...")
        files = list(self.input_dir.rglob("*.mid"))
        logging.info(f"Found {len(files)} MIDI files.")
        return files

    def _parse_midi_metadata(self, file_path):
        """
        Hierarchical metadata parsing for a single MIDI file.
        Returns a dict: {'work':..., 'composer':..., 'performer':...}
        """
        metadata = {}

        # --- Step 1: Internal Metadata (mido) ---
        try:
            mid = mido.MidiFile(file_path)
            if mid.tracks:
                # FIX 1: Iterate messages in the first track [2], not the list of tracks.
                for msg in mid.tracks: 
                    if msg.is_meta: # [1]
                        if msg.type == 'track_name' and 'work' not in metadata:
                            metadata['work'] = msg.name
                        # Copyright often has composer info
                        elif msg.type == 'copyright' and 'composer' not in metadata:
                            # Simple heuristic
                            if "by " in msg.text:
                                metadata['composer'] = msg.text.split("by ")[-1].strip()
        except Exception as e:
            logging.warning(f"Could not parse internal metadata for {file_path.name}: {e}")

        # --- Step 2: Filename Parsing (Regex) ---
        filename_stem = file_path.stem
        
        # If we still need data, try regex
        if 'work' not in metadata or 'composer' not in metadata:
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
        
        # --- Step 3: Fallbacks ---
        if 'work' not in metadata:
            metadata['work'] = filename_stem # Use the filename as a last resort
        if 'composer' not in metadata:
            metadata['composer'] = "Unknown Composer"
        if 'performer' not in metadata:
            metadata['performer'] = "Unknown Performer"

        logging.info(f"For {file_path.name} got: {metadata}")
        return metadata

    def build_data_model(self):
        """
        Builds the complete master data model for the entire library.
        """
        # FIX 2: Create directories *before* trying to copy files [3]
        self._setup_output_dirs()
        self._copy_static_assets()
        
        logging.info("Building data model...")
        midi_files = self._find_midi_files()

        model = {
            "composers": {},
            "performers": {},
            "files": {},
            "playlists": {}
        }

        for i, file_path in enumerate(midi_files):
            file_id = f"file-id-{i:06d}"
            logging.info(f"Processing {file_id}: {file_path.name}")
            
            # 1. Parse metadata
            metadata = self._parse_midi_metadata(file_path)
            
            # 2. Get/Create Composer
            composer_name = metadata['composer']
            composer_slug = slugify(composer_name)
            if composer_slug not in model['composers']:
                wiki_data = self.wiki_client.get_entity_data(composer_name, "composer")
                model['composers'][composer_slug] = {
                    "name": composer_name,
                    "slug": composer_slug,
                    "page_url": f"/composers/{composer_slug}.html",
                    "works": [],
                    **wiki_data
                }
            model['composers'][composer_slug]['works'].append(file_id)

            # 3. Get/Create Performer
            performer_name = metadata['performer']
            performer_slug = slugify(performer_name)
            if performer_slug not in model['performers']:
                wiki_data = self.wiki_client.get_entity_data(performer_name, "performer")
                model['performers'][performer_slug] = {
                    "name": performer_name,
                    "slug": performer_slug,
                    "page_url": f"/performers/{performer_slug}.html",
                    "works": [],
                    **wiki_data
                }
            model['performers'][performer_slug]['works'].append(file_id)

            # 4. Copy MIDI file to output
            output_midi_path = self.midi_files_dir / f"{file_id}.mid"
            shutil.copy(file_path, output_midi_path)

            # 5. Add File entry
            model['files'][file_id] = {
                "id": file_id,
                "title": metadata['work'],
                "composer_slug": composer_slug,
                "performer_slug": performer_slug,
                "page_url": f"/files/{file_id}.html",
                "midi_url": f"/midi-files/{file_id}.mid" # Relative URL
            }

        logging.info("Data model built. Generating playlists...")

        # 6. Generate Playlists
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

    def _render_template(self, template_name, context, output_path):
        """Helper to render a single Jinja2 template."""
        template = self.jinja_env.get_template(template_name)
        html = template.render(context)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html)

    def render_static_site(self, model):
        """
        Renders the complete static site from the data model.
        """
        # FIX 2: Removed _setup_output_dirs() and _copy_static_assets() from here
        
        logging.info("Rendering all HTML pages...")
        
        # 1. Render Index
        self._render_template(
            "index.html",
            {"model": model, "composers": sorted(model['composers'].values(), key=lambda x: x['name'])},
            self.output_dir / "index.html"
        )

        # 2. Render Composer Pages
        for slug, composer in model['composers'].items():
            playlist_json = json.dumps(model['playlists'].get(f"composer-{slug}", ))
            self._render_template(
                "composer.html",
                {"model": model, "composer": composer, "playlist_json": playlist_json},
                self.composers_dir / f"{slug}.html"
            )
            
        # 3. Render Performer Pages
        for slug, performer in model['performers'].items():
            playlist_json = json.dumps(model['playlists'].get(f"performer-{slug}", ))
            self._render_template(
                "performer.html",
                {"model": model, "performer": performer, "playlist_json": playlist_json},
                self.performers_dir / f"{slug}.html"
            )
            
        # 4. Render File Pages
        for file_id, file_data in model['files'].items():
            playlist = [{
                "url": file_data['midi_url'],
                "title": file_data['title']
            }]
            playlist_json = json.dumps(playlist)
            self._render_template(
                "file.html",
                {"model": model, "file": file_data, "playlist_json": playlist_json},
                self.files_dir / f"{file_id}.html"
            )
        
        logging.info("Site generation complete.")


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
        required=True,
        help="Path to the directory containing Jinja2 templates (base.html, etc.)"
    )
    parser.add_argument(
        '-o', '--output-dir',
        required=True,
        help="Path to the directory where the static site will be generated."
    )
    
    args = parser.parse_args()

    try:
        generator = MidiLibraryGenerator(
            args.input_dir,
            args.template_dir,
            args.output_dir
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
