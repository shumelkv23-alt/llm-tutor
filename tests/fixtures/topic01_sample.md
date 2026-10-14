---
jupytext:
  text_representation:
    extension: .md
    format_name: myst
---

(sample)=

# Sample Topic

Intro paragraph about the pandas library.

## 1. Demonstration

```{figure} /_static/img/sample.png
:name: sample_fig
```

We use `groupby` to aggregate a DataFrame.

```{code-cell} ipython3
# this comment must not become a heading
df = pd.read_csv("telecom_churn.csv")
```

## 2. Churn prediction

Predict churn using common sense alone.
