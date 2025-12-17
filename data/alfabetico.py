import os
import json
from typing import List, Set

# Percorso della cartella specificato dall'utente
CARTELLA_DA_ANALIZZARE = r"C:\Users\DarkArmy\Documents\listhdd\data" 

# Nome del file di output
NOME_FILE_OUTPUT = "elenco_film.txt"

# ----------------------------------------------------------------------
# FUNZIONI DI FILTRO E UTILITY
# ----------------------------------------------------------------------

def get_directory_path(percorso_completo_file: str) -> str:
    """Estrae il percorso della directory da un percorso file completo."""
    # Normalizza i separatori per coerenza e rimuovi il nome del file
    percorso_normalizzato = os.path.dirname(percorso_completo_file).replace('\\', '/')
    return percorso_normalizzato


# ----------------------------------------------------------------------
# FUNZIONE PRINCIPALE INTERATTIVA
# ----------------------------------------------------------------------

def estrai_e_ordina_film_interattivo(cartella_input: str) -> List[str]:
    """
    Analizza interattivamente i file JSON, chiedendo all'utente quali 
    percorsi di directory includere per ogni file.
    """
    nomi_film: Set[str] = set()
    
    # Lista delle cartelle già incluse globalmente (per non chiederle due volte nello stesso JSON)
    percorsi_globali_selezionati: Set[str] = set()
    
    try:
        json_files = sorted([f for f in os.listdir(cartella_input) if f.endswith('.json')])
    except FileNotFoundError as e:
        raise FileNotFoundError(f"La cartella specificata '{cartella_input}' non esiste o non è valida.") from e

    print(f"Trovati {len(json_files)} file JSON da analizzare. Inizia l'analisi interattiva.")

    for nome_file in json_files:
        percorso_completo_file = os.path.join(cartella_input, nome_file)
        
        try:
            with open(percorso_completo_file, 'r', encoding='utf-8') as f:
                dati = json.load(f)
        except (json.JSONDecodeError, Exception) as e:
            print(f"   [ERRORE] Impossibile leggere o decodificare il JSON di {nome_file}: {e}")
            continue

        # 1. Identifica tutti i percorsi di directory unici in questo file JSON
        unique_paths: Set[str] = set()
        
        if 'catalogo_video' in dati and isinstance(dati['catalogo_video'], list):
            for elemento in dati['catalogo_video']:
                percorso = elemento.get('percorso_completo', '')
                if percorso:
                    # Estrai il percorso della directory (es. 'F:/FILM/AZIONE')
                    dir_path = get_directory_path(percorso)
                    unique_paths.add(dir_path)
        else:
            print(f"   [AVVISO] Chiave 'catalogo_video' mancante o non valida in {nome_file}.")
            continue

        # 2. Interagisci con l'utente per scegliere quali percorsi includere
        percorsi_da_includere_in_file: Set[str] = set()
        print("\n" + "="*80)
        print(f"INTERATTIVO - Analisi file: {nome_file}")
        print(f"Trovati {len(unique_paths)} percorsi di directory unici.")
        print("="*80)
        
        for dir_path in sorted(list(unique_paths)):
            # Chiedi solo se il percorso non è stato scelto in un JSON precedente (ottimizzazione)
            # Nota: Ho rimosso l'ottimizzazione globale per seguire rigorosamente la tua richiesta
            # di chiedere "man mano che analizzi i vari json", quindi chiedo per ogni percorso trovato.
            
            # Formatta il percorso per renderlo più leggibile nel prompt
            prompt_path = dir_path.replace('/', '\\')
            
            risposta = input(f"-> Vuoi includere i file dal percorso:\n   '{prompt_path}'? (s/n): ").lower()
            if risposta == 's':
                percorsi_da_includere_in_file.add(dir_path)
                
        if not percorsi_da_includere_in_file:
            print(f"Nessun percorso selezionato per {nome_file}. Continuo con il prossimo...")
            continue
            
        # 3. Raccogli i nomi dei film che si trovano nei percorsi scelti
        film_aggiunti_in_file = 0
        for elemento in dati['catalogo_video']:
            nome = elemento.get('nome', '')
            percorso = elemento.get('percorso_completo', '')
            dir_path = get_directory_path(percorso)
            
            # Controlla se il percorso della directory è stato scelto DALL'UTENTE
            if nome and dir_path in percorsi_da_includere_in_file:
                nomi_film.add(nome)
                film_aggiunti_in_file += 1
        
        print(f"-> Aggiunti {film_aggiunti_in_file} film da {nome_file} al catalogo finale.")

    # Restituisce la lista globale e ordinata
    return sorted(list(nomi_film))


def salva_risultato_su_file(lista_film: List[str], nome_file: str):
    """Salva la lista dei film ordinati in un file di testo."""
    try:
        with open(nome_file, 'w', encoding='utf-8') as f:
            f.write("="*70 + "\n")
            f.write(f"🎬 ELENCO FILM ORDINATO (Totale: {len(lista_film)} film)\n")
            f.write(f"  Filtro usato: Scelta interattiva per ogni percorso di directory\n")
            f.write("="*70 + "\n")
            if lista_film:
                for i, film in enumerate(lista_film, 1):
                    f.write(f"{i}. {film}\n")
            else:
                f.write("Nessun film selezionato o trovato.\n")
            f.write("="*70 + "\n")
        
        print(f"\n✨ Operazione completata! L'elenco è stato salvato in: {os.path.abspath(nome_file)}")

    except Exception as e:
        print(f"\n[ERRORE] Impossibile salvare il file di output {nome_file}: {e}")


# --- Esecuzione dello script ---

if __name__ == "__main__":
    print(f"Inizio l'analisi interattiva dei file JSON nella cartella: {CARTELLA_DA_ANALIZZARE}")
    
    try:
        # 1. Estrai e ordina la lista dei film in modo interattivo
        risultato = estrai_e_ordina_film_interattivo(CARTELLA_DA_ANALIZZARE)
        
        # 2. Salva il risultato nel file di testo
        salva_risultato_su_file(risultato, NOME_FILE_OUTPUT)
        
    except FileNotFoundError as e:
        print(f"\nERRORE: {e}")
        print("Il programma è terminato. Controlla che il percorso sia scritto correttamente.")