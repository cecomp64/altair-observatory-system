# api_revision 4: a night master's calibrated subs as one zip in the archive
# (metadata only: the zip itself stays in S3 and is offered as a presigned link).
class AddCalibratedBundleToDataProducts < ActiveRecord::Migration[8.0]
  def change
    add_column :data_products, :calibrated_bundle, :jsonb
  end
end
