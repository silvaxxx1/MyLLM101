import torch
import torch.nn as nn

class GroupQueryAttention(nn.Module):
    def __init__(self, d_in, d_out, num_heads, num_groups, context_length, dropout=0.0, qkv_bias=False):
        super().__init__()

        assert d_out % num_heads == 0, "embed_dim is indivisible by num_heads"
        assert num_heads % num_groups == 0, "num_heads must be divisible by num_groups"

        self.num_heads = num_heads
        self.num_groups = num_groups
        self.context_length = context_length
        self.head_dim = d_out // num_heads

        # Separate projections for queries, but shared for keys/values within groups
        self.q_proj = nn.Linear(d_in, d_out, bias=qkv_bias)  # Queries (unique per head)
        self.kv_proj = nn.Linear(d_in, 2 * self.head_dim * num_groups, bias=qkv_bias)  # Shared keys/values per group

        self.proj = nn.Linear(d_out, d_out)
        self.dropout = nn.Dropout(dropout)

        # Register a buffer for the causal mask
        self.register_buffer(
            "mask", torch.triu(torch.ones(context_length, context_length), diagonal=1).bool()
        )

    def forward(self, x):
        batch_size, num_tokens, embed_dim = x.shape

        # Project queries (unique per head)
        queries = self.q_proj(x)  # (b, num_tokens, d_out)
        queries = queries.view(batch_size, num_tokens, self.num_heads, self.head_dim)
        queries = queries.permute(0, 2, 1, 3)  # (b, num_heads, num_tokens, head_dim)

        # Project shared keys/values per group
        kv = self.kv_proj(x)  # (b, num_tokens, 2 * head_dim * num_groups)
        kv = kv.view(batch_size, num_tokens, self.num_groups, 2, self.head_dim)
        kv = kv.permute(2, 3, 0, 1, 4)  # (num_groups, 2, b, num_tokens, head_dim)
        keys, values = kv[:, 0], kv[:, 1]  # (num_groups, b, num_tokens, head_dim)

        # Expand keys/values for all heads in each group
        keys = keys.unsqueeze(2).repeat(1, 1, self.num_heads // self.num_groups, 1, 1)  # (num_groups, b, heads_per_group, num_tokens, head_dim)
        values = values.unsqueeze(2).repeat(1, 1, self.num_heads // self.num_groups, 1, 1)  # (num_groups, b, heads_per_group, num_tokens, head_dim)

        # Reshape keys/values to match the query shape
        keys = keys.view(batch_size, self.num_heads, num_tokens, self.head_dim)  # (b, num_heads, num_tokens, head_dim)
        values = values.view(batch_size, self.num_heads, num_tokens, self.head_dim)  # (b, num_heads, num_tokens, head_dim)

        # Compute attention scores
        attn_scores = queries @ keys.transpose(-2, -1)  # (b, num_heads, num_tokens, num_tokens)

        # Dynamically resize the mask to match the number of tokens
        causal_mask = self.mask[:num_tokens, :num_tokens]  # (num_tokens, num_tokens)
        attn_scores = attn_scores.masked_fill(causal_mask, -torch.inf)

        # Softmax + dropout
        attn_weights = torch.softmax(attn_scores / keys.shape[-1]**0.5, dim=-1)
        attn_weights = self.dropout(attn_weights)

        # Compute context vector
        context_vec = attn_weights @ values  # (b, num_heads, num_tokens, head_dim)
        context_vec = context_vec.transpose(1, 2)  # (b, num_tokens, num_heads, head_dim)
        context_vec = context_vec.contiguous().view(batch_size, num_tokens, -1)  # (b, num_tokens, d_out)

        # Final projection
        context_vec = self.proj(context_vec)
        return context_vec

