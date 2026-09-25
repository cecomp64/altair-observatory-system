require "rails_helper"
require "sqlite3"

RSpec.describe Imports::AstroDb do
  let(:dir) { Pathname(Dir.mktmpdir) }
  let(:path) { dir.join("astrophotography.db").to_s }
  let(:user) { create(:user) }
  let(:telescope) { create(:telescope) }

  before do
    telescope.default_optical_train.update!(filters: [ { "name" => "Ha", "aliases" => [ "H-alpha" ] }, { "name" => "L" } ])
    db = SQLite3::Database.new(path)
    db.execute_batch(<<~SQL)
      CREATE TABLE objects (id INTEGER PRIMARY KEY, primary_name TEXT, ra REAL, dec REAL, object_type TEXT, magnitude REAL,
                            size_major REAL, size_minor REAL, constellation TEXT);
      CREATE TABLE object_aliases (id INTEGER PRIMARY KEY, object_id INTEGER, alias_name TEXT, catalog TEXT);
      CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT, description TEXT, status TEXT, priority INTEGER);
      CREATE TABLE project_targets (id INTEGER PRIMARY KEY, project_id INTEGER, object_id INTEGER, is_primary INTEGER,
                                    exposure_goals TEXT, notes TEXT);
      CREATE TABLE images (id INTEGER PRIMARY KEY, exposure_time REAL, filter_name TEXT);
      CREATE TABLE project_images (id INTEGER PRIMARY KEY, project_id INTEGER, image_id INTEGER);
      CREATE TABLE object_showcases (id INTEGER PRIMARY KEY, object_id INTEGER, source_type TEXT, file_path TEXT, survey_name TEXT);
      INSERT INTO objects VALUES (1, 'Andromeda Galaxy', 10.6848, 41.2690, 'Galaxy', 3.4, 190, 60, 'And');
      INSERT INTO objects VALUES (2, 'My Comet Field', 150.0, 20.0, NULL, NULL, NULL, NULL, NULL);
      INSERT INTO objects VALUES (3, 'Jupiter', NULL, NULL, 'Planet', NULL, NULL, NULL, NULL);
      INSERT INTO object_aliases VALUES (1, 1, 'M 31', 'Messier'), (2, 1, 'NGC 224', 'NGC');
      INSERT INTO projects VALUES (1, 'Andromeda', 'Deep LRGB', 'active', 3);
      INSERT INTO project_targets VALUES (1, 1, 1, 1, '{"Ha": 7200, "L": 3600, "OIII": 1800}', 'core'), (2, 1, 2, 0, '{"L": 600}', NULL);
      INSERT INTO images VALUES (1, 600, 'Ha'), (2, 600, 'Ha'), (3, 900, 'Ha');
      INSERT INTO project_images VALUES (1, 1, 1), (2, 1, 2), (3, 1, 3);
      INSERT INTO object_showcases VALUES (1, 1, 'upload', 'uploads/1.jpg', NULL);
    SQL
    db.close
    dir.join("showcases/uploads").mkpath
    dir.join("showcases/uploads/1.jpg").binwrite("\xFF\xD8\xFF\xE0fake-jpeg".b)
  end

  after { FileUtils.rm_rf(dir) }

  it "merges known objects, creates draft targets with plans from goals, and attaches showcases" do
    m31 = create(:astro_object, primary_name: "Andromeda Galaxy", ra_deg: 10.68479, dec_deg: 41.26906, source: "openngc")

    report = described_class.new(path, user: user, telescope: telescope).run

    expect(report).to have_attributes(objects_merged: 1, objects_created: 1, objects_skipped: 1, projects_created: 1,
                                      targets_created: 2, plans_created: 3, showcases: 1)
    expect(report.warnings.join).to include("OIII")
    project = user.projects.sole
    expect(project).to have_attributes(name: "Andromeda", status: "active", priority: 3)
    primary = project.targets.find_by!(astro_object: m31)
    expect(primary).to be_draft
    ha = primary.exposure_plans.find_by!(filter: "Ha")
    expect(ha).to have_attributes(exposure_seconds: 600, desired_count: 12) # median 600 s, 7200 s goal
    expect(primary.exposure_plans.find_by!(filter: "L")).to have_attributes(exposure_seconds: 300, desired_count: 12)
    expect(m31.reload.alias_names).to include("M 31", "NGC 224")
    expect(m31.showcase.image).to be_attached
  end

  it "keeps projects in planning with their goals when no telescope is given" do
    described_class.new(path, user: user).run
    project = user.projects.sole
    expect(project.status).to eq("planning")
    expect(project.targets).to be_empty
    expect(project.description).to include("Andromeda Galaxy: Ha 2.0 h, L 1.0 h, OIII 0.5 h")
  end

  it "doesn't merge a same-named object more than 1 arcminute away" do
    create(:astro_object, primary_name: "Andromeda Galaxy", ra_deg: 12.0, dec_deg: 41.0)
    report = described_class.new(path, user: user).run
    expect(report.objects_created).to eq(2)
  end

  it "skips projects that were already imported" do
    described_class.new(path, user: user, telescope: telescope).run
    report = described_class.new(path, user: user, telescope: telescope).run
    expect(report.projects_skipped).to eq(1)
    expect(user.projects.count).to eq(1)
  end
end
