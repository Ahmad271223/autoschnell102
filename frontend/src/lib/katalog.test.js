import { ladeMakes, katalogVergessen } from "./katalog";

beforeEach(() => katalogVergessen());

test("zweiter Aufruf loest keinen zweiten Request aus", async () => {
  const get = jest.fn().mockResolvedValue({ data: [{ id: 1, name: "Audi", models: [] }] });
  const a = await ladeMakes({ get });
  const b = await ladeMakes({ get });
  expect(get).toHaveBeenCalledTimes(1);
  expect(a).toBe(b);
});

test("nach einem Fehler wird beim naechsten Mal neu geladen", async () => {
  const get = jest.fn()
    .mockRejectedValueOnce(new Error("Netz weg"))
    .mockResolvedValueOnce({ data: [{ id: 2, name: "BMW", models: [] }] });
  await expect(ladeMakes({ get })).rejects.toThrow("Netz weg");
  const b = await ladeMakes({ get });
  expect(get).toHaveBeenCalledTimes(2);
  expect(b[0].name).toBe("BMW");
});
