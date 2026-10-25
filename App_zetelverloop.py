import streamlit as st
import pandas as pd
from pathlib import Path
from itertools import combinations
import json

st.set_page_config(page_title="Fracties · Tweede Kamer Monitor", page_icon=":material/account_balance:", layout="wide")

st.title("Fracties")

st.divider()
st.header("Fractie-relaties en overstappers")
st.write(
    "Een verbinding tussen twee fracties geeft aan hoeveel verschillende Kamerleden "
    "in beide fracties hebben gezeten. De groepskleuren tonen verbonden groepen bij "
    "de gekozen drempel; dit zijn geen politieke of ideologische clusters. PRO, "
    "GroenLinks-PvdA, PvdA en GroenLinks tellen als één fractie. Fracties die vóór "
    "1 januari 2010 zijn opgeheven, zijn uitgesloten."
)

csv_directory = Path(__file__).parent / "gold_csv"
required_files = {
    "wisselingen": csv_directory / "fact_zetelwisseling.csv",
    "fracties": csv_directory / "dim_fractie.csv",
    "personen": csv_directory / "dim_persoon.csv",
    "zetelbeeld": csv_directory / "agg_zetelbeeld_per_jaar.csv",
}

if all(path.is_file() for path in required_files.values()):
    fact_df = pd.read_csv(required_files["wisselingen"])
    faction_df = pd.read_csv(required_files["fracties"])
    person_df = pd.read_csv(required_files["personen"])

    faction_df["datum_actief"] = pd.to_datetime(faction_df["datum_actief"], errors="coerce")
    faction_df["datum_actief"] = pd.to_datetime(faction_df["datum_actief"], errors="coerce")
    faction_df["datum_inactief"] = pd.to_datetime(faction_df["datum_inactief"], errors="coerce")
    eligible_factions = faction_df[
        faction_df["datum_inactief"].isna() | faction_df["datum_inactief"].ge("2010-01-01")
    ]
    merged_pro_labels = {"pro", "progressief nederland", "groenlinks-pvda", "groenlinks", "gl", "pvda", "partij van de arbeid"}
    party_key_by_id, party_label_by_key = {}, {}
    for faction in eligible_factions.to_dict("records"):
        labels = {str(faction[column]).strip().casefold() for column in ("afkorting", "naam_nl", "fractie_norm") if pd.notna(faction[column])}
        key = "merged-pro" if labels & merged_pro_labels else faction["id"]
        party_key_by_id[faction["id"]] = key
        party_label_by_key[key] = "PRO / GroenLinks-PvdA" if key == "merged-pro" else faction["afkorting"] or faction["naam_nl"]

    person_names = person_df.set_index("id").apply(
        lambda row: " ".join(str(value) for value in (row["roepnaam"], row["tussenvoegsel"], row["achternaam"]) if pd.notna(value) and str(value).strip()),
        axis=1,
    ).to_dict()
    eligible_fact_df = fact_df[fact_df["fractie_id"].isin(party_key_by_id)].copy()
    eligible_fact_df["fractie_key"] = eligible_fact_df["fractie_id"].map(party_key_by_id)
    member_parties = eligible_fact_df[["persoon_id", "fractie_key"]].dropna().drop_duplicates()
    members_by_party = member_parties.groupby("fractie_key")["persoon_id"].apply(set)

    shared_members = {}
    for person_id, memberships in member_parties.groupby("persoon_id")["fractie_key"]:
        for pair in combinations(sorted(set(memberships)), 2):
            shared_members.setdefault(pair, set()).add(person_id)

    st.caption(f"{len(eligible_factions)} fractierecords actief sinds 2010; {len(members_by_party)} fracties na het samenvoegen van PRO-labels.")

    seat_df = pd.read_csv(required_files["zetelbeeld"])
    eligible_names = set(eligible_factions["fractie_norm"].dropna().astype(str))
    seat_df = seat_df[seat_df["fractie_norm"].isin(eligible_names)]
    available_factions = sorted(seat_df["fractie_norm"].unique())
    if available_factions:
        chart_column, stats_column = st.columns(2)
        with chart_column:
            st.subheader("Zetelverloop per fractie")
            selected_faction = st.selectbox("Kies een fractie", available_factions)
            faction_seats = seat_df.loc[
                seat_df["fractie_norm"].eq(selected_faction), ["jaar", "zetels"]
            ].sort_values("jaar")
            st.bar_chart(faction_seats, x="jaar", y="zetels", x_label="Jaar", y_label="Zetels")

        active_factions = eligible_factions[eligible_factions["datum_inactief"].isna()].copy()
        stats_factions = active_factions.assign(
            fractie_key=eligible_factions["id"].map(party_key_by_id)
        ).groupby("fractie_key", as_index=False).agg(start=("datum_actief", "min"))
        stats_factions["age_years"] = (
            pd.Timestamp.today().normalize() - stats_factions["start"]
        ).dt.days / 365.2425
        oldest = stats_factions.loc[stats_factions["start"].idxmin()]
        youngest = stats_factions.loc[stats_factions["start"].idxmax()]
        biggest = fact_df.loc[fact_df["zetels_op_datum"].idxmax()]
        biggest_faction = faction_df.set_index("id").loc[biggest["fractie_id"]]
        biggest_label = biggest_faction["afkorting"] or biggest_faction["naam_nl"]

        with stats_column:
            st.subheader("Fractie in cijfers")
            for title, label, years in (
                ("Oudste nog actieve fractie", party_label_by_key[oldest["fractie_key"]], oldest["age_years"]),
                ("Jongste nog actieve fractie", party_label_by_key[youngest["fractie_key"]], youngest["age_years"]),
            ):
                with st.container(border=True):
                    st.metric(title, label, f"{years:.1f} jaar")
            with st.container(border=True):
                st.metric("Grootste fractie ooit", biggest_label, f"{int(biggest['zetels_op_datum'])} zetels")
                st.caption(f"Op {biggest['datum_id']}")
    else:
        st.info("Er zijn geen jaarlijkse zetelgegevens voor fracties die sinds 2010 actief zijn.")

    max_overlap = max(map(len, shared_members.values()), default=1)
    overlap_threshold = st.slider("Minimaal aantal gedeelde leden voor een verbinding", 1, max_overlap, min(3, max_overlap))
    visible_edges = {pair: members for pair, members in shared_members.items() if len(members) >= overlap_threshold}

    party_ids = sorted(members_by_party.index, key=lambda key: party_label_by_key[key])
    adjacency = {party: set() for party in party_ids}
    for first, second in visible_edges:
        adjacency[first].add(second)
        adjacency[second].add(first)
    components, unvisited = [], set(party_ids)
    while unvisited:
        component, pending = set(), [min(unvisited, key=lambda key: party_label_by_key[key])]
        while pending:
            party = pending.pop()
            if party in unvisited:
                unvisited.remove(party)
                component.add(party)
                pending.extend(adjacency[party] & unvisited)
        components.append(sorted(component, key=lambda key: party_label_by_key[key]))
    components.sort(key=lambda group: (-len(group), party_label_by_key[group[0]]))

    palette = ["#e8a23a", "#4c9f70", "#5876a8", "#bc6c8d", "#9273b5", "#4f9da6"]
    node_id = {party: f"party_{index}" for index, party in enumerate(party_ids)}
    dot_lines = [
        "graph {",
        'graph [layout="fdp", start="42", overlap="false", splines="true", bgcolor="transparent"];',
        'node [shape="ellipse", style="filled", fillcolor="lightblue", color="#59636e", fontname="Arial"];',
        'edge [color="#89939e", fontname="Arial", fontsize="10"];',
    ]
    for number, group in enumerate(components):
        color = palette[number % len(palette)]
        if len(group) > 1:
            dot_lines.append(f'subgraph cluster_{number} {{ label="Groep {number + 1}"; color="{color}"; style="rounded";')
        dot_lines.extend(f'{node_id[party]} [label={json.dumps(party_label_by_key[party], ensure_ascii=False)}];' for party in group)
        dot_lines.extend(
            f'{node_id[first]} -- {node_id[second]} [label="{len(members)}", penwidth="{1 + min(len(members), 10) / 2:.1f}"];'
            for (first, second), members in visible_edges.items() if first in group
        )
        if len(group) > 1:
            dot_lines.append("}")
    dot_lines.append("}")

    graph_column, movers_column = st.columns([1.5, 1])
    with graph_column:
        st.subheader("Netwerk van fracties")
        st.caption("Dikkere verbindingen en grotere getallen betekenen meer gedeelde leden.")
        st.graphviz_chart("\n".join(dot_lines), width="stretch")
    with movers_column:
        st.subheader("Leden met de meeste fractiewissels")
        registrations = fact_df[fact_df["event_type"].eq("aanmelding")].sort_values(["persoon_id", "datum_id", "event_id"])
        switches, member_factions = {}, {}
        for person_id, rows in registrations.groupby("persoon_id", sort=False):
            keys = rows["fractie_id"].map(party_key_by_id).tolist()
            switches[person_id] = sum(a in party_key_by_id.values() and b in party_key_by_id.values() and a != b for a, b in zip(keys, keys[1:]))
            member_factions[person_id] = ", ".join(dict.fromkeys(party_label_by_key[key] for key in keys if key in party_label_by_key))
        movers_df = pd.DataFrame(
            [{"Kamerlid": person_names.get(person, person), "Fractiewissels": count, "Fracties": member_factions[person]} for person, count in switches.items() if count > 0]
        ).sort_values(["Fractiewissels", "Kamerlid"], ascending=[False, True])
        st.bar_chart(movers_df.head(10), x="Kamerlid", y="Fractiewissels", horizontal=True)
        st.dataframe(movers_df.head(10), hide_index=True, width="stretch")
        st.caption("Een wissel telt wanneer een aanmelding een andere fractie betreft dan de vorige aanmelding.")

    st.subheader("Gedeelde leden per fractiepaar")
    relationship_rows = [
        {"Fractie 1": party_label_by_key[first], "Fractie 2": party_label_by_key[second],
         "Gedeelde leden": len(members), "Namen": ", ".join(sorted(person_names.get(person, person) for person in members))}
        for (first, second), members in visible_edges.items()
    ]
    relationship_df = pd.DataFrame(relationship_rows)
    if not relationship_df.empty:
        relationship_df = relationship_df.sort_values(
            ["Gedeelde leden", "Fractie 1", "Fractie 2"], ascending=[False, True, True]
        )
        st.dataframe(relationship_df.head(3), hide_index=True, width="stretch")
    else:
        st.info("Er zijn geen fractieparen met zoveel gedeelde leden. Verlaag de drempel om meer verbindingen te zien.")
else:
    missing_files = [path.name for path in required_files.values() if not path.is_file()]
    st.error(f"Ontbrekende bestanden voor de fractieanalyse: {', '.join(missing_files)}")
