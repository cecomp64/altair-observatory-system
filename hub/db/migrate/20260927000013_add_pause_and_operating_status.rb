class AddPauseAndOperatingStatus < ActiveRecord::Migration[8.0]
  def change
    # A paused target keeps its status (the contract's enum is unchanged) and
    # simply isn't handed to the rig agent until it is resumed.
    add_column :targets, :paused_at, :datetime

    # What an admin says about a telescope, shown on the observatory page.
    add_column :telescopes, :operating_status, :string, null: false, default: "operational"
    add_column :telescopes, :status_note, :text
    add_column :telescopes, :status_changed_at, :datetime
  end
end
