class AddSharedToAstroObjects < ActiveRecord::Migration[8.0]
  def change
    # Only custom objects use it: they're private to their creator unless shared.
    # Catalogue imports (OpenNGC, LDN, LBN) and Telescopius lookups are always public.
    add_column :astro_objects, :shared, :boolean, null: false, default: false
  end
end
