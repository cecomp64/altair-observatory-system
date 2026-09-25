# This file is auto-generated from the current state of the database. Instead
# of editing this file, please use the migrations feature of Active Record to
# incrementally modify your database, and then regenerate this schema definition.
#
# This file is the source Rails uses to define your schema when running `bin/rails
# db:schema:load`. When creating a new database, `bin/rails db:schema:load` tends to
# be faster and is potentially less error prone than running all of your
# migrations from scratch. Old migrations may fail to apply correctly if those
# migrations use external dependencies or application code.
#
# It's strongly recommended that you check this file into your version control system.

ActiveRecord::Schema[8.1].define(version: 2026_09_26_000008) do
  # These are extensions that must be enabled in order to support this database
  enable_extension "pg_catalog.plpgsql"
  enable_extension "pg_trgm"

  create_table "active_storage_attachments", force: :cascade do |t|
    t.bigint "blob_id", null: false
    t.datetime "created_at", null: false
    t.string "name", null: false
    t.bigint "record_id", null: false
    t.string "record_type", null: false
    t.index ["blob_id"], name: "index_active_storage_attachments_on_blob_id"
    t.index ["record_type", "record_id", "name", "blob_id"], name: "index_active_storage_attachments_uniqueness", unique: true
  end

  create_table "active_storage_blobs", force: :cascade do |t|
    t.bigint "byte_size", null: false
    t.string "checksum"
    t.string "content_type"
    t.datetime "created_at", null: false
    t.string "filename", null: false
    t.string "key", null: false
    t.text "metadata"
    t.string "service_name", null: false
    t.index ["key"], name: "index_active_storage_blobs_on_key", unique: true
  end

  create_table "active_storage_variant_records", force: :cascade do |t|
    t.bigint "blob_id", null: false
    t.string "variation_digest", null: false
    t.index ["blob_id", "variation_digest"], name: "index_active_storage_variant_records_uniqueness", unique: true
  end

  create_table "api_keys", force: :cascade do |t|
    t.boolean "active", default: true, null: false
    t.datetime "created_at", null: false
    t.datetime "last_used_at"
    t.string "name", null: false
    t.bigint "owner_id", null: false
    t.string "owner_type", null: false
    t.string "scopes", default: [], null: false, array: true
    t.string "token_digest", null: false
    t.datetime "updated_at", null: false
    t.index ["owner_type", "owner_id"], name: "index_api_keys_on_owner_type_and_owner_id"
    t.index ["token_digest"], name: "index_api_keys_on_token_digest", unique: true
  end

  create_table "astro_objects", force: :cascade do |t|
    t.string "constellation"
    t.datetime "created_at", null: false
    t.bigint "created_by_id"
    t.decimal "dec_deg", precision: 9, scale: 5
    t.decimal "magnitude", precision: 5, scale: 2
    t.string "object_type"
    t.decimal "position_angle_deg", precision: 6, scale: 2
    t.string "primary_name", null: false
    t.decimal "ra_deg", precision: 9, scale: 5
    t.decimal "size_major_arcmin", precision: 8, scale: 2
    t.decimal "size_minor_arcmin", precision: 8, scale: 2
    t.string "source", default: "custom", null: false
    t.string "source_ref"
    t.datetime "updated_at", null: false
    t.index ["constellation"], name: "index_astro_objects_on_constellation"
    t.index ["created_by_id"], name: "index_astro_objects_on_created_by_id"
    t.index ["dec_deg"], name: "index_astro_objects_on_dec_deg"
    t.index ["object_type"], name: "index_astro_objects_on_object_type"
    t.index ["primary_name"], name: "index_astro_objects_on_primary_name_trgm", opclass: :gin_trgm_ops, using: :gin
  end

  create_table "data_products", force: :cascade do |t|
    t.bigint "altair_id"
    t.string "archive_uri"
    t.datetime "captured_at"
    t.datetime "created_at", null: false
    t.string "filter"
    t.integer "kind", default: 0, null: false
    t.jsonb "metrics", default: {}, null: false
    t.string "nas_path"
    t.date "night"
    t.bigint "optical_train_id"
    t.bigint "processing_node_id"
    t.bigint "project_id"
    t.string "sha256"
    t.bigint "size_bytes"
    t.bigint "superseded_by_id"
    t.bigint "target_id", null: false
    t.datetime "updated_at", null: false
    t.string "url"
    t.integer "version"
    t.index ["optical_train_id"], name: "index_data_products_on_optical_train_id"
    t.index ["processing_node_id", "kind", "altair_id"], name: "index_data_products_on_node_kind_altair_id", unique: true, where: "(altair_id IS NOT NULL)"
    t.index ["processing_node_id"], name: "index_data_products_on_processing_node_id"
    t.index ["project_id"], name: "index_data_products_on_project_id"
    t.index ["target_id"], name: "index_data_products_on_target_id"
  end

  create_table "exposure_plans", force: :cascade do |t|
    t.integer "collected_count", default: 0, null: false
    t.integer "completed_count", default: 0, null: false
    t.datetime "created_at", null: false
    t.integer "desired_count", default: 1, null: false
    t.integer "exposure_seconds", null: false
    t.string "filter", null: false
    t.integer "integrated_count", default: 0, null: false
    t.decimal "integrated_seconds", precision: 12, scale: 2, default: "0.0", null: false
    t.bigint "target_id", null: false
    t.datetime "updated_at", null: false
    t.integer "usable_count", default: 0, null: false
    t.index ["target_id"], name: "index_exposure_plans_on_target_id"
  end

  create_table "object_aliases", force: :cascade do |t|
    t.bigint "astro_object_id", null: false
    t.string "catalog"
    t.datetime "created_at", null: false
    t.string "name", null: false
    t.string "normalized_name", null: false
    t.datetime "updated_at", null: false
    t.index ["astro_object_id", "normalized_name"], name: "index_object_aliases_on_astro_object_id_and_normalized_name", unique: true
    t.index ["astro_object_id"], name: "index_object_aliases_on_astro_object_id"
    t.index ["normalized_name"], name: "index_object_aliases_on_normalized_name"
    t.index ["normalized_name"], name: "index_object_aliases_on_normalized_name_trgm", opclass: :gin_trgm_ops, using: :gin
  end

  create_table "object_showcases", force: :cascade do |t|
    t.bigint "astro_object_id", null: false
    t.datetime "created_at", null: false
    t.bigint "data_product_id"
    t.string "source_type", default: "upload", null: false
    t.string "survey_name"
    t.datetime "updated_at", null: false
    t.index ["astro_object_id"], name: "index_object_showcases_on_astro_object_id", unique: true
  end

  create_table "optical_trains", force: :cascade do |t|
    t.boolean "active", default: true, null: false
    t.string "bayer_pattern"
    t.string "camera_name"
    t.string "camera_type", default: "mono", null: false
    t.datetime "created_at", null: false
    t.jsonb "filters", default: [], null: false
    t.decimal "focal_length_mm", precision: 8, scale: 2
    t.boolean "has_rotator", default: false, null: false
    t.jsonb "header_aliases", default: {}, null: false
    t.string "key", null: false
    t.string "name", null: false
    t.decimal "pixel_size_um", precision: 6, scale: 3
    t.integer "sensor_height_px"
    t.integer "sensor_width_px"
    t.bigint "telescope_id", null: false
    t.datetime "updated_at", null: false
    t.index ["telescope_id", "key"], name: "index_optical_trains_on_telescope_id_and_key", unique: true
    t.index ["telescope_id"], name: "index_optical_trains_on_telescope_id"
  end

  create_table "processing_node_telescopes", force: :cascade do |t|
    t.datetime "created_at", null: false
    t.bigint "processing_node_id", null: false
    t.bigint "telescope_id", null: false
    t.datetime "updated_at", null: false
    t.index ["processing_node_id", "telescope_id"], name: "index_processing_node_telescopes_uniqueness", unique: true
    t.index ["processing_node_id"], name: "index_processing_node_telescopes_on_processing_node_id"
    t.index ["telescope_id"], name: "index_processing_node_telescopes_on_telescope_id"
  end

  create_table "processing_nodes", force: :cascade do |t|
    t.boolean "active", default: true, null: false
    t.datetime "created_at", null: false
    t.text "description"
    t.datetime "last_heartbeat_at"
    t.string "name", null: false
    t.jsonb "status", default: {}, null: false
    t.datetime "updated_at", null: false
    t.index ["name"], name: "index_processing_nodes_on_name", unique: true
  end

  create_table "projects", force: :cascade do |t|
    t.string "completion_basis", default: "acquired", null: false
    t.datetime "created_at", null: false
    t.text "description"
    t.string "name", null: false
    t.integer "priority", default: 0, null: false
    t.jsonb "processing_settings", default: {}, null: false
    t.string "status", default: "planning", null: false
    t.datetime "updated_at", null: false
    t.bigint "user_id", null: false
    t.string "visibility", default: "private", null: false
    t.index ["status"], name: "index_projects_on_status"
    t.index ["user_id"], name: "index_projects_on_user_id"
  end

  create_table "target_events", force: :cascade do |t|
    t.datetime "created_at", null: false
    t.integer "event_type", null: false
    t.jsonb "payload", default: {}, null: false
    t.bigint "target_id", null: false
    t.datetime "updated_at", null: false
    t.index ["target_id"], name: "index_target_events_on_target_id"
  end

  create_table "targets", force: :cascade do |t|
    t.bigint "astro_object_id"
    t.datetime "created_at", null: false
    t.decimal "dec_deg", precision: 9, scale: 5, null: false
    t.boolean "is_primary", default: true, null: false
    t.decimal "min_altitude_deg", precision: 5, scale: 2
    t.string "name", null: false
    t.text "notes"
    t.bigint "optical_train_id"
    t.string "panel"
    t.string "preview_image_url"
    t.integer "priority", default: 0, null: false
    t.jsonb "processing_settings", default: {}, null: false
    t.bigint "project_id", null: false
    t.decimal "ra_deg", precision: 9, scale: 5, null: false
    t.decimal "rotation_deg", precision: 6, scale: 2
    t.string "schedule_count_basis"
    t.integer "status", default: 0, null: false
    t.datetime "submitted_at"
    t.bigint "telescope_id", null: false
    t.datetime "updated_at", null: false
    t.bigint "user_id", null: false
    t.index ["astro_object_id"], name: "index_targets_on_astro_object_id"
    t.index ["optical_train_id"], name: "index_targets_on_optical_train_id"
    t.index ["project_id"], name: "index_targets_on_project_id"
    t.index ["status"], name: "index_targets_on_status"
    t.index ["telescope_id"], name: "index_targets_on_telescope_id"
    t.index ["user_id"], name: "index_targets_on_user_id"
  end

  create_table "telescopes", force: :cascade do |t|
    t.boolean "active", default: true, null: false
    t.datetime "created_at", null: false
    t.bigint "default_optical_train_id"
    t.text "description"
    t.decimal "elevation_m", precision: 7, scale: 2
    t.decimal "latitude", precision: 8, scale: 5, null: false
    t.decimal "longitude", precision: 8, scale: 5, null: false
    t.decimal "min_altitude_deg", precision: 5, scale: 2, default: "30.0", null: false
    t.string "name", null: false
    t.boolean "self_serve_submit", default: false, null: false
    t.string "slug", null: false
    t.string "timezone", null: false
    t.datetime "updated_at", null: false
    t.index ["slug"], name: "index_telescopes_on_slug", unique: true
  end

  create_table "users", force: :cascade do |t|
    t.datetime "created_at", null: false
    t.string "discord_webhook_url"
    t.string "email", default: "", null: false
    t.string "encrypted_password", default: "", null: false
    t.string "name", default: "", null: false
    t.boolean "notify_discord", default: false, null: false
    t.boolean "notify_email", default: true, null: false
    t.datetime "remember_created_at"
    t.datetime "reset_password_sent_at"
    t.string "reset_password_token"
    t.integer "role", default: 0, null: false
    t.string "sjaa_membership_number"
    t.datetime "updated_at", null: false
    t.index ["email"], name: "index_users_on_email", unique: true
    t.index ["reset_password_token"], name: "index_users_on_reset_password_token", unique: true
    t.index ["role"], name: "index_users_on_role"
  end

  add_foreign_key "active_storage_attachments", "active_storage_blobs", column: "blob_id"
  add_foreign_key "active_storage_variant_records", "active_storage_blobs", column: "blob_id"
  add_foreign_key "astro_objects", "users", column: "created_by_id"
  add_foreign_key "data_products", "optical_trains"
  add_foreign_key "data_products", "processing_nodes"
  add_foreign_key "data_products", "projects"
  add_foreign_key "data_products", "targets"
  add_foreign_key "exposure_plans", "targets"
  add_foreign_key "object_aliases", "astro_objects", on_delete: :cascade
  add_foreign_key "object_showcases", "astro_objects", on_delete: :cascade
  add_foreign_key "optical_trains", "telescopes"
  add_foreign_key "processing_node_telescopes", "processing_nodes"
  add_foreign_key "processing_node_telescopes", "telescopes"
  add_foreign_key "projects", "users"
  add_foreign_key "target_events", "targets"
  add_foreign_key "targets", "astro_objects"
  add_foreign_key "targets", "optical_trains"
  add_foreign_key "targets", "projects"
  add_foreign_key "targets", "telescopes"
  add_foreign_key "targets", "users"
  add_foreign_key "telescopes", "optical_trains", column: "default_optical_train_id"
end
