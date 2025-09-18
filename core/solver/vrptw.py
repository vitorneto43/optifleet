from typing import List, Dict, Tuple
from ortools.constraint_solver import pywrapcp, routing_enums_pb2

def solve_vrptw(*, time_matrix_min: List[List[float]], dist_matrix_km: List[List[float]],
                depot_index: int, service_times: List[int], demands: List[int],
                time_windows: List[Tuple[int,int]], vehicles: List[dict],
                objective: str = "min_cost") -> Dict:
    n_nodes = len(time_matrix_min)
    manager = pywrapcp.RoutingIndexManager(n_nodes, len(vehicles), depot_index)
    routing = pywrapcp.RoutingModel(manager)

    def time_callback(from_index, to_index):
        i = manager.IndexToNode(from_index)
        j = manager.IndexToNode(to_index)
        return int(round(time_matrix_min[i][j]))

    transit_cb = routing.RegisterTransitCallback(time_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_cb)

    routing.AddDimension(transit_cb, 60*24, 60*24, False, "Time")
    time_dim = routing.GetDimensionOrDie("Time")

    for node in range(n_nodes):
        tw = time_windows[node]
        idx = manager.NodeToIndex(node)
        time_dim.CumulVar(idx).SetRange(int(tw[0]), int(tw[1]))

    for node in range(n_nodes):
        idx = manager.NodeToIndex(node)
        time_dim.SlackVar(idx).SetValue(service_times[node])

    def demand_cb(from_index):
        i = manager.IndexToNode(from_index)
        return int(demands[i])
    demand_idx = routing.RegisterUnaryTransitCallback(demand_cb)
    routing.AddDimensionWithVehicleCapacity(
        demand_idx, 0, [int(v["capacity"]) for v in vehicles], True, "Capacity"
    )

    for v_id, v in enumerate(vehicles):
        time_dim.CumulVar(routing.Start(v_id)).SetRange(v["start_min"], v["end_min"])
        time_dim.CumulVar(routing.End(v_id)).SetRange(v["start_min"], v["end_min"])

    search_params = pywrapcp.DefaultRoutingSearchParameters()
    search_params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    search_params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    search_params.time_limit.seconds = 30

    solution = routing.SolveWithParameters(search_params)
    if not solution:
        return {"status": "infeasible"}

    routes, total_time, total_km = [], 0, 0.0
    for v_id in range(len(vehicles)):
        idx = routing.Start(v_id)
        nodes, rtime, rkm = [], 0, 0.0
        while not routing.IsEnd(idx):
            node = manager.IndexToNode(idx)
            nodes.append(node)
            nxt_idx = solution.Value(routing.NextVar(idx))
            if not routing.IsEnd(nxt_idx):
                nxt = manager.IndexToNode(nxt_idx)
                rtime += time_matrix_min[node][nxt]
                rkm += dist_matrix_km[node][nxt]
            idx = nxt_idx
        nodes.append(manager.IndexToNode(idx))
        total_time += rtime
        total_km += rkm
        routes.append({"vehicle_id": vehicles[v_id]["id"], "nodes": nodes, "time_min": round(rtime,1), "dist_km": round(rkm,3)})
    return {"status":"ok", "routes": routes, "total_time_min": round(total_time,1), "total_dist_km": round(total_km,3)}
