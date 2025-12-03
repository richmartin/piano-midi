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
import requests
import wikipedia
from datetime import datetime
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
        
        # Generate Build ID
        self.build_id = datetime.now().strftime("%Y%m%d-%H%M%S")

    def _setup_output_dirs(self):
        """Creates output directories if they don't exist."""
        logging.info(f"Ensuring output directories exist at: {self.output_dir}")
        
        self.output_dir.mkdir(exist_ok=True)
        self.static_dir.mkdir(exist_ok=True)
        self.midi_files_dir.mkdir(exist_ok=True)
        self.composers_dir.mkdir(exist_ok=True)
        self.performers_dir.mkdir(exist_ok=True)
        self.files_dir.mkdir(exist_ok=True)

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

    def _extract_pianists_from_pdfs(self):
        """Scans input_dir for PDFs, converts to text, and extracts pianist info."""
        mapping = {}
        pdfs = list(self.input_dir.glob("*.pdf"))
        if not pdfs:
            logging.info("No PDF files found in input directory for pianist extraction.")
            return mapping

        logging.info(f"Found {len(pdfs)} PDF files. Extracting pianist data...")
        
        # Regex to find "Name (YYYY-YYYY)" lines
        pattern = re.compile(r'^(.+?)\s+\((\d{4}-\d{4})\)\s*$')

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
                    match = pattern.match(line)
                    if match:
                        full_name = match.group(1).strip()
                        dates = match.group(2).strip()
                        display_name = f"{full_name} ({dates})"
                        
                        # Heuristic for Short Name
                        parts = full_name.split()
                        last_name = parts[-1]
                        
                        if len(parts) > 1 and parts[-2].lower() == "d'albert":
                             last_name = "d'Albert"
                        elif len(parts) > 1 and parts[-2].lower() == "von":
                            last_name = f"von {last_name}"

                        short_slug = slugify(last_name)
                        if short_slug not in mapping:
                            mapping[short_slug] = display_name
                        
                        if "d'albert" in full_name.lower():
                             mapping["dalbert"] = display_name

            except subprocess.CalledProcessError as e:
                logging.error(f"Failed to convert {pdf_file}: {e}")
            except Exception as e:
                logging.error(f"Error extracting from {pdf_file}: {e}")
        
        logging.info(f"Extracted {len(mapping)} pianists from PDFs.")
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

    def _smart_copy(self, src, dst):
        """
        Copies src to dst only if dst doesn't exist or is different.
        Checks file size and modification time.
        """
        if not dst.exists():
            shutil.copy2(src, dst) # copy2 preserves metadata
            return True
        
        src_stat = src.stat()
        dst_stat = dst.stat()
        
        # Check size
        if src_stat.st_size != dst_stat.st_size:
            shutil.copy2(src, dst)
            return True
            
        # Check mtime (if src is newer than dst)
        if src_stat.st_mtime > dst_stat.st_mtime:
            shutil.copy2(src, dst)
            return True
            
        return False

    def build_data_model(self):
        """
        Builds the complete master data model for the entire library.
        """
        # FIX 2: Create directories *before* trying to copy files [3]
        self._setup_output_dirs()
        self._copy_static_assets()
        
        logging.info("Building data model...")
        midi_files = self._find_midi_files()
        
        # Load mappings from CSV and PDFs
        csv_mapping = self._load_pianist_mapping()
        pdf_mapping = self._extract_pianists_from_pdfs()
        
        # Merge mappings (PDFs take precedence)
        pianist_mapping = {**csv_mapping, **pdf_mapping}

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
            metadata = self._parse_metadata(file_path)
            
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
            
            # Use full name from mapping if available
            if performer_slug in pianist_mapping:
                performer_name = pianist_mapping[performer_slug]
            
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
                "composer_slug": composer_slug,
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

    def _render_template(self, template_name, context, output_path):
        """Helper to render a single Jinja2 template."""
        template = self.jinja_env.get_template(template_name)
        # Inject build_id into context
        context['build_id'] = self.build_id
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
