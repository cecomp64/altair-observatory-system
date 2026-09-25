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

ActiveRecord::Schema[8.1].define(version: 2026_09_20_114918) do
  # These are extensions that must be enabled in order to support this database
  enable_extension "pg_catalog.plpgsql"

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
    t.bigint "telescope_id", null: false
    t.string "token_digest", null: false
    t.datetime "updated_at", null: false
    t.index ["telescope_id"], name: "index_api_keys_on_telescope_id"
    t.index ["token_digest"], name: "index_api_keys_on_token_digest", unique: true
  end

  create_table "exposure_plans", force: :cascade do |t|
    t.integer "completed_count", default: 0, null: false
    t.datetime "created_at", null: false
    t.integer "desired_count", default: 1, null: false
    t.integer "exposure_seconds", null: false
    t.string "filter", null: false
    t.bigint "target_id", null: false
    t.datetime "updated_at", null: false
    t.index ["target_id"], name: "index_exposure_plans_on_target_id"
  end

  create_table "target_events", force: :cascade do |t|
    t.datetime "created_at", null: false
    t.integer "event_type", null: false
    t.jsonb "payload", default: {}, null: false
    t.bigint "target_id", null: false
    t.datetime "updated_at", null: false
    t.index ["target_id"], name: "index_target_events_on_target_id"
  end

  create_table "target_files", force: :cascade do |t|
    t.datetime "captured_at"
    t.datetime "created_at", null: false
    t.string "filter"
    t.integer "kind", default: 0, null: false
    t.bigint "target_id", null: false
    t.datetime "updated_at", null: false
    t.string "url", null: false
    t.index ["target_id"], name: "index_target_files_on_target_id"
  end

  create_table "targets", force: :cascade do |t|
    t.datetime "created_at", null: false
    t.decimal "dec_deg", precision: 9, scale: 5, null: false
    t.string "name", null: false
    t.text "notes"
    t.string "preview_image_url"
    t.integer "priority", default: 0, null: false
    t.decimal "ra_deg", precision: 9, scale: 5, null: false
    t.integer "status", default: 0, null: false
    t.datetime "submitted_at"
    t.bigint "telescope_id", null: false
    t.datetime "updated_at", null: false
    t.bigint "user_id", null: false
    t.index ["status"], name: "index_targets_on_status"
    t.index ["telescope_id"], name: "index_targets_on_telescope_id"
    t.index ["user_id"], name: "index_targets_on_user_id"
  end

  create_table "telescopes", force: :cascade do |t|
    t.boolean "active", default: true, null: false
    t.datetime "created_at", null: false
    t.text "description"
    t.decimal "elevation_m", precision: 7, scale: 2
    t.decimal "latitude", precision: 8, scale: 5, null: false
    t.decimal "longitude", precision: 8, scale: 5, null: false
    t.string "name", null: false
    t.boolean "self_serve_submit", default: false, null: false
    t.string "slug", null: false
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
  add_foreign_key "api_keys", "telescopes"
  add_foreign_key "exposure_plans", "targets"
  add_foreign_key "target_events", "targets"
  add_foreign_key "target_files", "targets"
  add_foreign_key "targets", "telescopes"
  add_foreign_key "targets", "users"
end
