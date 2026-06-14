import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def save_histogram(values, out_path, title, xlabel):
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.hist(values, bins=min(max(len(values), 5), 12), edgecolor='white', alpha=0.85)
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel('Frequency')
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


def save_line_chart(x_values, y_values, out_path, title, xlabel, ylabel, invert_x=False):
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(x_values, y_values, linewidth=1.0)
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    if invert_x:
        ax.invert_xaxis()
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path
