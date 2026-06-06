# Supabase Migration Sketch

SQLite remains the current runtime database. When moving to Supabase, keep the same logical tables and API methods used by `database/sqlite_db.py`.

## Tables

```sql
create table pinterest_images (
  id bigint generated always as identity primary key,
  url text not null unique,
  local_path text,
  drive_path text,
  image_hash text,
  scraped_at timestamptz not null default now(),
  used boolean not null default false,
  used_at timestamptz
);

create table products_cache (
  id bigint generated always as identity primary key,
  sheet_row_index bigint not null unique,
  name text not null,
  image_url text not null,
  mulebuy_link text not null,
  category text not null,
  price numeric not null default 0,
  tags text not null default '',
  popularity_score int not null default 0,
  last_synced timestamptz not null default now()
);

create table posts (
  id bigint generated always as identity primary key,
  category text not null,
  pinterest_image_url text,
  pinterest_local_path text,
  pinterest_image_id bigint references pinterest_images(id),
  product_ids jsonb not null default '[]'::jsonb,
  image_paths_json jsonb not null default '[]'::jsonb,
  product_image_paths_json jsonb not null default '[]'::jsonb,
  public_image_urls_json jsonb not null default '[]'::jsonb,
  captions_json jsonb not null default '{}'::jsonb,
  formatted_captions_json jsonb not null default '{}'::jsonb,
  carousel_image_count int not null default 0,
  caption text,
  hashtags text,
  video_path text,
  drive_folder_id text,
  status text not null default 'draft',
  created_at timestamptz not null default now(),
  approved_at timestamptz,
  posted_at timestamptz
);

create table post_platforms (
  id bigint generated always as identity primary key,
  post_id bigint not null references posts(id),
  platform text not null,
  status text not null default 'pending',
  platform_post_id text,
  posted_at timestamptz,
  error_message text,
  unique(post_id, platform)
);

create table used_products (
  id bigint generated always as identity primary key,
  post_id bigint not null references posts(id),
  product_sheet_row bigint not null
);

create table music_usage (
  id bigint generated always as identity primary key,
  track_path text not null,
  used_at timestamptz not null default now()
);
```

## Implementation Contract

`SupabaseDatabase` should implement the same public methods as `SqliteDatabase`:

- `insert_pinterest_image`
- `is_duplicate_image`
- `get_unused_pinterest_images`
- `mark_pinterest_image_used`
- `count_unused_pinterest_images`
- `sync_products`
- `get_all_cached_products`
- `get_recently_used_product_rows`
- `create_post`
- `get_post`
- `get_recent_posts`
- `get_posts_by_status`
- `mark_post_status`
- `update_post_captions`
- `record_used_products`
- `upsert_platform_status`
- `record_music_usage`
- `get_recently_used_tracks`
- `get_stats`
