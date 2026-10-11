class AddSchedulingSettingsToProjects < ActiveRecord::Migration[8.1]
  # Target Scheduler project settings the rig agent passes on (api_revision 6):
  # exposures per filter before switching (0 = finish a filter first) and
  # exposures of a filter between dithers (0 = no dithering).
  def change
    add_column :projects, :filter_switch_frequency, :integer, null: false, default: 0
    add_column :projects, :dither_every, :integer, null: false, default: 4
  end
end
