def extraire_urls_quotidien(fichier_source, fichier_destination):
    urls_gardees = []

    with open(fichier_source, "r", encoding="utf-8") as f:
        for ligne in f:
            # On découpe la ligne selon la tabulation (\t)
            colonnes = ligne.rstrip("\r\n").split("\t")

            if colonnes:
                premiere_colonne = colonnes[0].strip()

                # On vérifie :
                # 1. Que c'est bien une URL (commence par http)
                # 2. Qu'elle contient le mot 'Quotidien' (insensible à la casse)
                if (
                    premiere_colonne.startswith("http")
                    and "quotidien" in premiere_colonne.lower()
                ):
                    urls_gardees.append(premiere_colonne)

    # Écriture du résultat final
    with open(fichier_destination, "w", encoding="utf-8") as f:
        for url in urls_gardees:
            f.write(url + "\n")

    print(f"Extraction terminée : {len(urls_gardees)} URL(s) trouvée(s).")


# Exemple d'utilisation :
extraire_urls_quotidien("C:\\Users\\ASUS\\Downloads\\files\\new_logic\\dgcmef_doc.txt", "urls_quotidien.txt")