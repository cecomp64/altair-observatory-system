# The parsed horizon file, cached so pages and visibility calculations don't
# download the attachment each time. Telescope refreshes it when a file is
# attached. An existing file that doesn't parse gets no points (a new upload
# like it is rejected).
class AddHorizonPointsToTelescopes < ActiveRecord::Migration[8.0]
  def up
    add_column :telescopes, :horizon_points, :jsonb, null: false, default: []

    Telescope.reset_column_information
    Telescope.joins(:horizon_file_attachment).find_each do |telescope|
      points = HorizonFileParser.parse(telescope.horizon_file.download)
      telescope.update_column(:horizon_points, points)
    rescue HorizonFileParser::Error => e
      say "#{telescope.slug}: horizon file #{e.message}; no horizon points cached"
    end
  end

  def down
    remove_column :telescopes, :horizon_points
  end
end
