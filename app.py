"""Roteirizador CVRP - Trabalho final (TEP00132 - UFF)

Etapa 1: enderecos -> geocodificacao -> matriz de distancias
Etapa 2: frota de veiculos
Etapa 3: roteamento (VRPSolverEasy) e mapa das rotas

Rodar:  streamlit run app.py
"""
import time

import folium
import pandas as pd
import requests
import streamlit as st
from geopy.distance import geodesic
from geopy.geocoders import Nominatim
from streamlit_folium import st_folium

OSRM = "https://router.project-osrm.org"
CORES = ["red", "blue", "green", "purple", "orange", "darkred", "cadetblue",
         "darkgreen", "black", "pink"]

st.set_page_config(page_title="Roteirizador CVRP", layout="wide")
ss = st.session_state
ss.setdefault("etapa", 1)


# ---------------------------------------------------------------- utilidades
@st.cache_data(show_spinner=False)
def geocodificar(endereco: str):
    geo = Nominatim(user_agent="roteirizador-cvrp-uff-aluno")
    for consulta in (endereco, endereco + ", Brasil"):
        try:
            loc = geo.geocode(consulta, timeout=10)
        except Exception:
            loc = None
        time.sleep(1.1)  # limite do Nominatim: 1 req/s
        if loc:
            return loc.latitude, loc.longitude
    return None


def matriz_distancias(coords):
    """Matriz em metros. Usa OSRM (ruas reais); se falhar, usa distancia geodesica."""
    try:
        pts = ";".join(f"{lon},{lat}" for lat, lon in coords)
        r = requests.get(f"{OSRM}/table/v1/driving/{pts}",
                         params={"annotations": "distance"}, timeout=30)
        m = r.json()["distances"]
        return [[int(round(x)) for x in linha] for linha in m], "OSRM (ruas)"
    except Exception:
        n = len(coords)
        m = [[int(geodesic(coords[i], coords[j]).meters) for j in range(n)]
             for i in range(n)]
        return m, "Distancia em linha reta (OSRM indisponivel)"


def tracado_osrm(a, b):
    """Geometria da rua entre dois pontos; fallback: linha reta."""
    try:
        url = f"{OSRM}/route/v1/driving/{a[1]},{a[0]};{b[1]},{b[0]}"
        r = requests.get(url, params={"overview": "full", "geometries": "geojson"},
                         timeout=15)
        return [(y, x) for x, y in r.json()["routes"][0]["geometry"]["coordinates"]]
    except Exception:
        return [a, b]


# ------------------------------------------------------------------- solver
def resolver_vrpsolvereasy(dist, demandas, frota, limite):
    import VRPSolverEasy as vrp

    modelo = vrp.Model()
    big = int(max(max(l) for l in dist) * len(dist) * 2 + 1000)
    for i, v in enumerate(frota, start=1):
        modelo.add_vehicle_type(id=i, start_point_id=0, end_point_id=0,
                                name=v["nome"], capacity=int(v["capacidade"]),
                                max_number=int(v["quantidade"]),
                                var_cost_dist=1, tw_end=big)
    modelo.add_depot(id=0, name="Deposito", tw_begin=0, tw_end=big)
    for i in range(1, len(dist)):
        modelo.add_customer(id=i, name=f"Cliente {i}", demand=int(demandas[i]),
                            tw_begin=0, tw_end=big)
    for i in range(len(dist)):
        for j in range(len(dist)):
            if i != j:
                modelo.add_link(start_point_id=i, end_point_id=j,
                                distance=dist[i][j], time=dist[i][j])
    modelo.set_parameters(time_limit=limite, solver_name="CLP")
    modelo.solve()
    if not modelo.solution.is_defined:
        return None, None
    info = {"valor": modelo.solution.value,
            "lb": modelo.statistics.best_lb,
            "tempo": modelo.statistics.solution_time,
            "msg": modelo.message}
    return [list(r.point_ids) for r in modelo.solution.routes], info


def resolver_savings(dist, demandas, frota):
    """Fallback: Clarke & Wright (sem dependencia externa)."""
    cap = max(v["capacidade"] for v in frota)
    n = len(dist)
    rotas = {i: [i] for i in range(1, n)}
    onde = {i: i for i in range(1, n)}
    ganhos = sorted(((dist[0][i] + dist[0][j] - dist[i][j], i, j)
                     for i in range(1, n) for j in range(i + 1, n)), reverse=True)
    for g, i, j in ganhos:
        if g <= 0:
            break
        ri, rj = onde[i], onde[j]
        if ri == rj:
            continue
        A, B = rotas[ri], rotas[rj]
        if sum(demandas[k] for k in A + B) > cap:
            continue
        if A[-1] == i and B[0] == j:
            nova = A + B
        elif B[-1] == j and A[0] == i:
            nova = B + A
        elif A[0] == i and B[0] == j:
            nova = A[::-1] + B
        elif A[-1] == i and B[-1] == j:
            nova = A + B[::-1]
        else:
            continue
        rotas[ri] = nova
        del rotas[rj]
        for k in nova:
            onde[k] = ri
    return [[0] + r + [0] for r in rotas.values()]


# ---------------------------------------------------------------- ETAPA 1
if ss.etapa == 1:
    st.title("Etapa 1: Enderecos")
    st.caption("Informe os pontos. Marque 'Deposito' em exatamente um ponto "
               "(demanda 0). Clientes precisam de demanda > 0. "
               "Ex.: 'Rua da Conceicao 100, Centro, Niteroi, RJ'.")
    base = pd.DataFrame({
        "Endereco": ["Rua Passo da Patria 156, Niteroi, RJ",
                     "Rua Presidente Backer 337, Niteroi, RJ",
                     "Rua da Conceicao 100, Niteroi, RJ",
                     "Avenida Roberto Silveira 512, Niteroi, RJ"],
        "Demanda": [0, 100, 80, 70],
        "Deposito": [True, False, False, False]})
    df = st.data_editor(ss.get("df_pontos", base), num_rows="dynamic",
                        use_container_width=True, key="editor_pontos")

    if st.button("Geocodificar e Avancar", type="primary"):
        df = df.dropna(subset=["Endereco"]).reset_index(drop=True)
        dep = df.index[df["Deposito"] == True].tolist()  # noqa: E712
        if len(dep) != 1:
            st.error("Marque exatamente um ponto como Deposito.")
            st.stop()
        # deposito sempre no indice 0
        df = pd.concat([df.loc[dep], df.drop(dep)]).reset_index(drop=True)
        if len(df) - 1 > 49:
            st.error("Maximo de 49 clientes (alem do deposito).")
            st.stop()
        if (df.loc[1:, "Demanda"] <= 0).any():
            st.error("Todos os clientes devem ter demanda > 0.")
            st.stop()
        coords, falhas = [], []
        barra = st.progress(0.0, text="Geocodificando...")
        for i, end in enumerate(df["Endereco"]):
            res = geocodificar(end)
            if res is None:
                falhas.append(end)
            coords.append(res)
            barra.progress((i + 1) / len(df))
        if falhas:
            st.error("Nao consegui geocodificar: " + "; ".join(falhas) +
                     ". Tente completar rua, numero, cidade e estado.")
            st.stop()
        dist, fonte = matriz_distancias(coords)
        ss.update(df_pontos=df, coords=coords, dist=dist, fonte_dist=fonte, etapa=2)
        st.rerun()

# ---------------------------------------------------------------- ETAPA 2
elif ss.etapa == 2:
    st.title("Etapa 2: Frota de veiculos")
    st.caption(f"Matriz de distancias calculada com: {ss.fonte_dist}")
    demanda_total = int(ss.df_pontos["Demanda"].sum())
    st.info(f"Demanda total dos clientes: {demanda_total}")
    frota_base = pd.DataFrame({"Nome": ["Caminhao"], "Capacidade": [200],
                               "Quantidade": [3]})
    frota_df = st.data_editor(ss.get("frota_df", frota_base), num_rows="dynamic",
                              use_container_width=True, key="editor_frota")
    ss.limite = st.number_input("Tempo limite do solver (segundos)", 5, 600,
                                ss.get("limite", 60))
    c1, c2 = st.columns(2)
    if c1.button("Voltar"):
        ss.etapa = 1
        st.rerun()
    if c2.button("Roteirizar", type="primary"):
        frota_df = frota_df.dropna().reset_index(drop=True)
        cap_total = (frota_df["Capacidade"] * frota_df["Quantidade"]).sum()
        if frota_df.empty:
            st.error("Cadastre pelo menos um veiculo.")
            st.stop()
        if frota_df["Capacidade"].max() < ss.df_pontos["Demanda"].max():
            st.error("Algum cliente tem demanda maior que a maior capacidade.")
            st.stop()
        if cap_total < demanda_total:
            st.error("Capacidade total da frota menor que a demanda total.")
            st.stop()
        ss.frota_df = frota_df
        ss.etapa = 3
        st.rerun()

# ---------------------------------------------------------------- ETAPA 3
else:
    st.title("Etapa 3: Roteamento")
    df, coords, dist = ss.df_pontos, ss.coords, ss.dist
    demandas = df["Demanda"].astype(int).tolist()
    frota = [{"nome": r.Nome, "capacidade": r.Capacidade, "quantidade": r.Quantidade}
             for r in ss.frota_df.itertuples()]

    if "rotas" not in ss:
        with st.spinner("Resolvendo..."):
            try:
                ss.rotas, ss.info = resolver_vrpsolvereasy(
                    dist, demandas, frota, ss.limite)
                ss.solver = "VRPSolverEasy (Branch-Cut-and-Price)"
            except ImportError:
                ss.rotas, ss.info = resolver_savings(dist, demandas, frota), None
                ss.solver = "Clarke & Wright (VRPSolverEasy nao instalado)"
            except Exception as e:
                st.warning(f"Erro no VRPSolverEasy ({e}); usando heuristica.")
                ss.rotas, ss.info = resolver_savings(dist, demandas, frota), None
                ss.solver = "Clarke & Wright (fallback)"

    rotas = ss.rotas
    if not rotas:
        st.error("O solver nao encontrou solucao viavel.")
    else:
        st.caption(f"Solver: {ss.solver}")
        linhas, total = [], 0
        mapa = folium.Map(location=coords[0], zoom_start=13)
        folium.Marker(coords[0], tooltip="Deposito",
                      icon=folium.Icon(color="black", icon="home")).add_to(mapa)
        for k, rota in enumerate(rotas):
            cor = CORES[k % len(CORES)]
            d = sum(dist[a][b] for a, b in zip(rota, rota[1:]))
            carga = sum(demandas[i] for i in rota)
            total += d
            linhas.append({"Rota": k + 1, "Sequencia": " -> ".join(map(str, rota)),
                           "Carga": carga, "Distancia (km)": round(d / 1000, 2)})
            for a, b in zip(rota, rota[1:]):
                folium.PolyLine(tracado_osrm(coords[a], coords[b]),
                                color=cor, weight=5, opacity=0.8).add_to(mapa)
            for i in rota[1:-1]:
                folium.Marker(coords[i], icon=folium.Icon(color=cor),
                              tooltip=f"{i}: {df['Endereco'][i]} (demanda {demandas[i]})"
                              ).add_to(mapa)
        c1, c2 = st.columns([2, 1])
        with c1:
            st_folium(mapa, width=800, height=550, returned_objects=[])
        with c2:
            st.metric("Distancia total", f"{total / 1000:.2f} km")
            st.metric("Veiculos usados", len(rotas))
            if ss.info:
                v, lb = ss.info["valor"], ss.info["lb"]
                gap = 100 * (v - lb) / v if v else 0
                st.metric("Melhor limite inferior", f"{lb / 1000:.2f} km")
                st.metric("Gap", f"{gap:.2f} %")
                st.caption(f"{ss.info['msg']} (tempo: {ss.info['tempo']:.1f} s)")
            st.dataframe(pd.DataFrame(linhas), hide_index=True)
            st.caption("Indice 0 = deposito. Clientes: " + "; ".join(
                f"{i}={df['Endereco'][i]}" for i in range(1, len(df))))

    if st.button("Recomecar"):
        for k in ("df_pontos", "coords", "dist", "frota_df", "rotas"):
            ss.pop(k, None)
        ss.etapa = 1
        st.rerun()
