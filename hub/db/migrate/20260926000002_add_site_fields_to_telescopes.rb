# Telescopes gain the site timezone (nights are local noon-to-noon in it) and a
# default minimum altitude. Existing rows get DEFAULT_TELESCOPE_TIMEZONE.
class AddSiteFieldsToTelescopes < ActiveRecord::Migration[8.1]
  def up
    add_column :telescopes, :timezone, :string
    add_column :telescopes, :min_altitude_deg, :decimal, precision: 5, scale: 2, default: 30, null: false
    add_column :telescopes, :default_optical_train_id, :bigint

    tz = ENV.fetch("DEFAULT_TELESCOPE_TIMEZONE", "America/Los_Angeles")
    execute "UPDATE telescopes SET timezone = #{connection.quote(tz)}"
    change_column_null :telescopes, :timezone, false
  end

  def down
    remove_column :telescopes, :default_optical_train_id
    remove_column :telescopes, :min_altitude_deg
    remove_column :telescopes, :timezone
  end
end
