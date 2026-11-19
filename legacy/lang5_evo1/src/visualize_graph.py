import sys
import os
import argparse
from causal_world_modelling_agent.visualization.graph_visualizer import GraphVisualizer
import networkx as nx
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

def save_graph_png(graph, output_dir, filename):
    """Save a NetworkX graph as a static PNG under output_dir."""
    import math
    import networkx as nx
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if graph is None or graph.number_of_nodes() == 0:
        raise ValueError("No graph nodes available for PNG export.")

    os.makedirs(output_dir, exist_ok=True)

    undirected = graph.to_undirected()
    partitions = list(
        nx.algorithms.community.louvain_communities(
            undirected,
            seed=42
        )
    )

    node_to_partition = {}
    for index, partition in enumerate(partitions):
        for node in partition:
            node_to_partition[node] = index

    colors = [
        plt.cm.tab20(index % 20)
        for index in range(len(partitions))
    ]
    node_colors = [
        colors[node_to_partition[node]]
        for node in graph.nodes()
    ]

    node_count = graph.number_of_nodes()
    if node_count <= 80:
        pos = nx.kamada_kawai_layout(undirected)
    else:
        pos = nx.spring_layout(
            undirected,
            seed=42,
            k=2.0 / math.sqrt(node_count),
            iterations=500
        )

    fig, ax = plt.subplots(
        figsize=(
            max(16, min(30, node_count * 0.32)),
            max(12, min(24, node_count * 0.24))
        )
    )

    nx.draw_networkx_nodes(
        graph,
        pos,
        node_color=node_colors,
        node_size=420,
        edgecolors="white",
        linewidths=0.6,
        alpha=0.95,
        ax=ax
    )

    edge_artists = nx.draw_networkx_edges(
        graph,
        pos,
        arrows=True,
        arrowstyle="-|>",
        arrowsize=14,
        width=1.8,
        edge_color="#34495e",
        alpha=0.9,
        connectionstyle="arc3,rad=0.06",
        ax=ax
    )

    label_artists = nx.draw_networkx_labels(
        graph,
        pos,
        font_size=6.5,
        font_color="black",
        bbox=dict(
            facecolor="white",
            edgecolor="none",
            alpha=0.7,
            pad=0.12
        ),
        ax=ax
    )

    for edge_artist in edge_artists:
        edge_artist.set_zorder(3)
    for label_artist in label_artists.values():
        label_artist.set_zorder(4)

    ax.set_title(
        f"Causal Graph ({node_count} nodes, {graph.number_of_edges()} edges)",
        fontsize=12
    )
    ax.axis("off")
    fig.tight_layout()

    output_path = os.path.join(output_dir, f"{filename}.png")
    fig.savefig(output_path, bbox_inches="tight", dpi=180)
    plt.close(fig)
    return os.path.abspath(output_path)

def save_graph_png_via_browser(visualizer, graph_path, output_dir, filename):
    """Render the existing web visualizer in headless Chrome and save a PNG."""
    import socket
    import subprocess
    import tempfile
    import threading
    import time
    import urllib.parse
    import urllib.request
    from flask import send_from_directory

    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.abspath(
        os.path.join(output_dir, f"{filename}.png")
    )

    index_path = os.path.join(visualizer.static_folder, 'index.html')

    @visualizer.app.route('/export-graph')
    def export_graph_view():
        with open(index_path, 'r', encoding='utf-8') as index_file:
            html = index_file.read()

        export_style = '''
        <style>
        html, body {
            width: 100%;
            height: 100%;
            margin: 0;
            padding: 0;
            overflow: hidden;
        }
        .app-container {
            width: 100vw !important;
            height: 100vh !important;
            max-width: none !important;
            margin: 0 !important;
            padding: 0 !important;
        }
        header, .search-panel, footer, #info-panel,
        #analysis-panel, #cluster-legend {
            display: none !important;
        }
        main {
            width: 100% !important;
            height: 100% !important;
            margin: 0 !important;
            padding: 0 !important;
        }
        .visualization-container {
            width: 100% !important;
            height: 100% !important;
            margin: 0 !important;
            padding: 0 !important;
            border-radius: 0 !important;
        }
        #graph-container {
            width: 100% !important;
            height: 100% !important;
        }
        </style>
        '''
        return html.replace('</head>', export_style + '</head>')

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]

    server_thread = threading.Thread(
        target=lambda: visualizer.app.run(
            host='127.0.0.1',
            port=port,
            debug=False,
            use_reloader=False,
            threaded=True
        ),
        daemon=True
    )
    server_thread.start()

    server_url = f'http://127.0.0.1:{port}'
    for _ in range(100):
        try:
            with urllib.request.urlopen(server_url, timeout=0.25):
                break
        except Exception:
            time.sleep(0.1)
    else:
        raise RuntimeError("Headless visualization server did not start.")

    chrome_candidates = [
        os.environ.get('CHROME_PATH'),
        r'C:\Program Files\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
        r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
    ]
    chrome_path = next(
        (candidate for candidate in chrome_candidates
         if candidate and os.path.exists(candidate)),
        None
    )
    if not chrome_path:
        raise RuntimeError("Chrome or Edge executable was not found.")

    graph_url = (
        f'{server_url}/export-graph?graph='
        f'{urllib.parse.quote(os.path.abspath(graph_path))}'
    )

    with tempfile.TemporaryDirectory(prefix='causal-graph-chrome-') as profile_dir:
        command = [
            chrome_path,
            '--headless=new',
            '--disable-gpu',
            '--hide-scrollbars',
            '--no-first-run',
            '--disable-extensions',
            '--force-device-scale-factor=1.5',
            '--window-size=2400,1600',
            '--virtual-time-budget=10000',
            f'--user-data-dir={profile_dir}',
            f'--screenshot={output_path}',
            graph_url
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=60
        )

    if result.returncode != 0 or not os.path.exists(output_path):
        raise RuntimeError(
            f"Headless browser screenshot failed: {result.stderr.strip()}"
        )

    return output_path

def main():
    """Main entry point for the graph visualizer command."""
    parser = argparse.ArgumentParser(
        description='Interactive visualization tool for causal graphs'
    )
    parser.add_argument(
        '-g', '--graph', 
        help='Path to a graph file to load on startup'
    )
    parser.add_argument(
        '-p', '--port', 
        type=int, 
        default=5000, 
        help='Port for the web server (default: 5000)'
    )
    parser.add_argument(
        '--no-browser', 
        dest='open_browser', 
        action='store_false',
        help='Do not automatically open browser'
    )
    parser.add_argument(
        '--debug', 
        action='store_true', 
        help='Run in debug mode'
    )

    parser.add_argument(
        '--output-dir',
        type=str,
        default='./data/png_output',
        help='Directory for exported PNG files (default: ./data/png_output)'
    )
    parser.add_argument(
        '--save-only',
        action='store_true',
        help='Save the PNG and exit without starting the web server'
    )

    args = parser.parse_args()

    # Validate graph file path if specified
    if args.graph and not os.path.exists(args.graph):
        print(f"Error: Graph file not found: {args.graph}")
        sys.exit(1)

    # print(f"Starting Causal Graph Visualizer on http://localhost:{args.port}")
    # print("Press Ctrl+C to stop the server")
    
    # Create a visualizer instance
    visualizer = GraphVisualizer(
        port=args.port,
        debug=args.debug,
        open_browser=args.open_browser
    )
    
    if args.graph:
        # Convert to absolute path if necessary
        abs_graph_path = os.path.abspath(args.graph)
        print(f"Loading graph from: {abs_graph_path}")
        
        # Store the absolute path to load on startup
        visualizer.graph_path = abs_graph_path
        
        # Preload the graph in memory to make sure it loads on startup
        try:
            import networkx as nx
            _, ext = os.path.splitext(abs_graph_path)
            if ext.lower() == '.gml':
                visualizer.graph = nx.read_gml(abs_graph_path)
            elif ext.lower() == '.graphml':
                visualizer.graph = nx.read_graphml(abs_graph_path)
            elif ext.lower() == '.gexf':
                visualizer.graph = nx.read_gexf(abs_graph_path)
            elif ext.lower() == '.json':
                import json
                with open(abs_graph_path, 'r') as f:
                    graph_data = json.load(f)
                visualizer.graph = nx.node_link_graph(graph_data)
            else:
                print(f"Warning: Unrecognized file extension {ext}. Attempting to read as GML.")
                visualizer.graph = nx.read_gml(abs_graph_path)
            
            print(f"Successfully loaded graph with {visualizer.graph.number_of_nodes()} nodes and {visualizer.graph.number_of_edges()} edges")
            output_name = os.path.splitext(os.path.basename(abs_graph_path))[0]
            # png_path = save_graph_png(
            #     visualizer.graph,
            #     args.output_dir,
            #     output_name
            # )
            # print(f"Successfully saved PNG to: {png_path}")
            try:
                png_path = save_graph_png_via_browser(
                    visualizer,
                    abs_graph_path,
                    args.output_dir,
                    output_name
                )
            except Exception as browser_error:
                print(
                    "Headless browser export failed. "
                    f"Falling back to Matplotlib: {browser_error}"
                )
                png_path = save_graph_png(
                    visualizer.graph,
                    args.output_dir,
                    output_name
                )
            print(f"Successfully saved PNG to: {png_path}")
        except Exception as e:
            print(f"Error preloading graph: {str(e)}")
            print("Will attempt to load again when server starts.")
    
    if args.save_only:
        return

    # Web display disabled: the script now only exports PNG files.

if __name__ == "__main__":
    main()