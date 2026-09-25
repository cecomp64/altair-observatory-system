# completed_count keeps meaning "acquired" (set by the worker). The other
# counters are recomputed by the Hub from frames and data products (§4.3).
class AddCountersToExposurePlans < ActiveRecord::Migration[8.1]
  def change
    add_column :exposure_plans, :collected_count, :integer, null: false, default: 0
    add_column :exposure_plans, :usable_count, :integer, null: false, default: 0
    add_column :exposure_plans, :integrated_count, :integer, null: false, default: 0
    add_column :exposure_plans, :integrated_seconds, :decimal, precision: 12, scale: 2, null: false, default: 0
  end
end
