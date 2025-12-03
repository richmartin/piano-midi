import re
import csv
import unicodedata

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

def extract_pianists(input_files, output_file):
    # Regex to find "Name (YYYY-YYYY)" lines
    pattern = re.compile(r'^(.+?)\s+\((\d{4}-\d{4})\)\s*$')
    
    pianists = {}

    for input_file in input_files:
        print(f"Processing {input_file}...")
        with open(input_file, 'r', encoding='utf-8') as f:
            for line in f:
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
                    
                    if short_slug not in pianists:
                        pianists[short_slug] = display_name
                    
                    if "d'albert" in full_name.lower():
                         pianists["dalbert"] = display_name

    # Write to CSV
    with open(output_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['Slug', 'DisplayName'])
        for slug, name in sorted(pianists.items()):
            writer.writerow([slug, name])
            
    print(f"Extracted {len(pianists)} unique pianists to {output_file}")

if __name__ == "__main__":
    files = ["baroque_list.txt", "romantic_list.txt", "20thc_list.txt"]
    extract_pianists(files, "pianists.csv")
